"""Trusted business guard reports survive preparation and approval rechecks."""
import os
from unittest.mock import patch

import pytest

from erp_harness.erp.invoice_eligibility import InvoiceEligibilityError
from erp_harness.erp.purchase_allocation import PurchaseAllocationError
from erp_harness.erp.task_evidence import TaskEvidence, TaskHandoff, failure_result
from tests.test_actions import _actions
from tests.test_invoice_mail import setup as setup_mail
from tests.test_purchase_allocation import seed


@pytest.mark.parametrize('reason', ['already_invoiced', 'awaiting_delivery'])
def test_invoice_public_guard_preserves_business_reason_and_actual_recovery_tool(reason):
    actions, writer, runtime = _actions()
    try:
        runtime.client.records['sale.order'][7].update(state='sale', invoice_ids=[301] if reason == 'already_invoiced' else [])
        runtime.client.records['sale.order.line'][71].update(qty_to_invoice=0, qty_delivered=0,
            qty_invoiced=3 if reason == 'already_invoiced' else 0)
        with patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'sale.advance.payment.inv.create_invoices'}):
            result = actions.call('execute_method', {'model': 'sale.advance.payment.inv',
                'method': 'create_invoices', 'kwargs': {'ids': [9]}})
        expected = 'invoice_eligibility_blocked' if reason == 'already_invoiced' else 'business_choice_required'
        assert result['reason_code'] == result['failure']['code'] == expected
        assert result['business_condition']['reason_code'] == reason
        assert result['error'] == result['business_condition']['next_step']
        assert result['failure']['layer'] == 'business_precondition'
        assert result['failure']['stage'] == 'before_send'
        assert result['failure']['write_dispatch_started'] is False
        assert result['approval_required'] is result['retry_safe'] is False
        assert result['recovery_request'] == {'tool': 'mcp_odoo_read_invoice_eligibility',
            'arguments': {'order_ids': [7], 'final': True, 'instance': 'default'}}
        if reason == 'awaiting_delivery':
            assert result['failure']['requires_user_input'] is True
            assert result['next_action'] == 'clarify_business_choice'
        else:
            assert result['next_action'] == 'read_invoice_eligibility'
        assert writer.calls == [] and actions.store.summary()['actions'] == 0
    finally:
        actions.store.close()


@pytest.mark.parametrize('kind', ['invoice', 'purchase', 'handoff'])
def test_host_approved_action_recheck_retains_typed_refusal_and_action_identity(tmp_path, kind):
    if kind == 'purchase':
        actions, writer, runtime = seed(tmp_path)
        runtime.client.records['purchase.order'][8]['origin'] = 'SO7, SO8'
        spec = {'version': 1, 'instruction_sha256': 'confirmed-demand', 'purchase_sources': [{
            'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
            'source': {'model': 'sale.order', 'domain': [['id', 'in', [7, 8]]], 'field': 'name'},
            'check_demand_capacity': True}]}
        arguments = {'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]}}
    else:
        actions, writer, runtime = _actions(path=tmp_path / 'actions.sqlite3', approval_mode='host')
        spec = {'version': 1, 'instruction_sha256': 'confirmed-scope'}
        if kind == 'invoice':
            runtime.client.records['sale.order'][7]['state'] = 'sale'
            arguments = {'model': 'sale.advance.payment.inv', 'method': 'create_invoices', 'kwargs': {'ids': [9]}}
        else:
            arguments = {'model': 'sale.order', 'method': 'action_confirm', 'kwargs': {'ids': [7]}}
    actions.approval_mode = 'host'
    actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
    original = actions._register

    def register_then_change(*args, **kwargs):
        row = original(*args, **kwargs)
        if kind == 'invoice':
            runtime.client.records['sale.order.line'][71].update(qty_to_invoice=0, qty_delivered=0)
        elif kind == 'purchase':
            runtime.client.records['purchase.order'][8]['origin'] = 'SO7'
        else:
            actions.task_evidence.spec['read_only'] = True
        return row

    try:
        with patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': arguments['model'] + '.' + arguments['method']}):
            pending = actions.call('execute_method', arguments)
            assert pending['approval_required'] is True
            action_id = pending['action_id']
            assert actions.store.approve(action_id, 'desktop_host')
            with patch.object(actions, '_register', side_effect=register_then_change):
                result = actions.call('execute_method', arguments)
        expected = {'invoice': 'business_choice_required', 'purchase': 'purchase_allocation_unverified',
                    'handoff': 'scope_handoff_required'}[kind]
        assert result['success'] is False and result['failure']['code'] == expected
        assert result['action_id'] == action_id and result['action_status'] == 'approved'
        assert actions.store.get(action_id)['status'] == 'approved'
        assert result['failure']['stage'] == 'before_send'
        assert result['failure']['write_dispatch_started'] is False
        assert result['approval_required'] is result['retry_safe'] is False
        if kind != 'handoff':
            assert result['business_condition']['status'] in {'blocked', 'failed'}
            assert result['recovery_request']['tool'].startswith('mcp_odoo_read_')
        else:
            assert result['failure']['requires_user_input'] is True
        assert writer.calls == [] and actions.store.summary()['actions'] == 1
    finally:
        actions.store.close()


@pytest.mark.parametrize('kind', ['invoice', 'purchase', 'handoff'])
@pytest.mark.parametrize('code,stage', [('action_outcome_unknown', 'send'), ('action_verification_failed', 'verification')])
def test_trusted_local_guard_never_overrides_post_send_uncertainty(kind, code, stage):
    error = (InvoiceEligibilityError({'reason_code': 'awaiting_delivery', 'requires_business_choice': True,
             'next_step': 'Wait for delivery or ask for a business choice.', 'orders': [{'id': 7}]}) if kind == 'invoice'
             else PurchaseAllocationError({'status': 'failed', 'checks': []}) if kind == 'purchase'
             else TaskHandoff('Scope changed; ask for renewed authority.'))
    result = failure_result(error, code=code, stage=stage, write_dispatch_started=True)
    assert result['reason_code'] == result['failure']['code'] == code
    assert result['failure']['stage'] == stage and result['failure']['write_dispatch_started'] is True
    assert result['next_action'] == 'reconcile_without_replay' and result['retry_safe'] is False
    assert 'recovery_request' not in result


def test_untrusted_invoice_error_subclass_cannot_publish_report():
    class ExternalInvoiceError(InvoiceEligibilityError):
        pass

    actions, writer, _ = _actions()
    error = ExternalInvoiceError({'next_step': 'private-external-instruction', 'reason_code': 'already_invoiced'})
    try:
        with patch.object(actions, '_prestate', side_effect=error), patch.dict(os.environ, {
                'ODOO_MCP_ENABLE_WRITES': '1', 'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'sale.advance.payment.inv.create_invoices'}):
            result = actions.call('execute_method', {'model': 'sale.advance.payment.inv',
                'method': 'create_invoices', 'kwargs': {'ids': [9]}})
        assert result['reason_code'] == 'tool_failed_unknown'
        assert 'business_condition' not in result and 'private-external' not in result['error']
        assert writer.calls == []
    finally:
        actions.store.close()


def test_untrusted_handoff_subclass_cannot_publish_body_or_recovery():
    class ExternalHandoff(TaskHandoff):
        pass

    result = failure_result(ExternalHandoff('private-external-instruction'))
    assert result['reason_code'] == 'tool_failed_unknown'
    assert 'private-external' not in result['error']
    assert 'requires_user_input' not in result['failure']


def test_unknown_invoice_delivery_retains_prior_dispatch_when_current_scope_refuses(tmp_path, monkeypatch):
    actions, writer, _ = setup_mail(tmp_path, monkeypatch)
    arguments = {'model': 'account.move', 'method': 'message_post', 'kwargs': {
        'ids': [10], 'partner_ids': [8], 'subject': '公司 · INV/001',
        'body': '李明您好，附件为 INV/001，金额 3,322.20 CNY。请查收，谢谢。',
    }}
    try:
        pending = actions.call('execute_method', arguments)
        action_id = pending['action_id']
        assert actions.store.approve(action_id, 'desktop_host')
        writer.execute_method = lambda *args, **kwargs: writer.calls.append(kwargs) or 201
        first = actions.call('execute_method', arguments)
        assert first['action_status'] == 'needs_reconciliation' and len(writer.calls) == 1
        actions.task_evidence.spec['read_only'] = True
        result = actions.call('execute_method', arguments)
        assert result['action_id'] == action_id and result['action_status'] == 'needs_reconciliation'
        assert result['reason_code'] == result['failure']['code'] == 'action_verification_failed'
        assert result['failure']['stage'] == 'verification' and result['failure']['write_dispatch_started'] is True
        assert result['next_action'] == 'reconcile_without_replay' and result['retry_safe'] is False
        assert 'recovery_request' not in result
        assert len(writer.calls) == actions.store.summary()['actions'] == 1
    finally:
        actions.store.close()


def test_invoice_recovery_retains_explicit_instance_and_final_choice():
    error = InvoiceEligibilityError({'next_step': 'Read existing invoices.', 'reason_code': 'already_invoiced',
        'requires_business_choice': False, 'orders': [{'id': 7}], 'instance': 'branch', 'final': False})
    result = failure_result(error)
    assert result['recovery_request'] == {'tool': 'mcp_odoo_read_invoice_eligibility',
        'arguments': {'order_ids': [7], 'final': False, 'instance': 'branch'}}


@pytest.mark.parametrize('kind', ['invoice', 'purchase'])
def test_real_business_guard_recovery_is_bound_to_the_executed_instance(tmp_path, kind):
    actions, writer, runtime = seed(tmp_path) if kind == 'purchase' else _actions(path=tmp_path / 'actions.sqlite3')
    runtime.instance = 'branch'
    actions.reads.instances['branch'] = runtime
    actions.clients['branch'] = writer
    try:
        if kind == 'purchase':
            spec = {'version': 1, 'instruction_sha256': 'branch-demand', 'instance': 'branch', 'purchase_sources': [{
                'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
                'source': {'model': 'sale.order', 'domain': [['id', 'in', [7, 8]]], 'field': 'name'},
                'check_demand_capacity': True}]}
            actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
            arguments = {'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]}, 'instance': 'branch'}
        else:
            runtime.client.records['sale.order'][7].update(state='sale', invoice_ids=[301])
            runtime.client.records['sale.order.line'][71].update(qty_to_invoice=0, qty_invoiced=3)
            arguments = {'model': 'sale.advance.payment.inv', 'method': 'create_invoices', 'kwargs': {'ids': [9]}, 'instance': 'branch'}
        with patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': arguments['model'] + '.' + arguments['method']}):
            result = actions.call('execute_method', arguments)
        assert result['success'] is False
        assert result['business_condition']['instance'] == 'branch'
        assert result['recovery_request']['arguments']['instance'] == 'branch'
        assert writer.calls == []
    finally:
        actions.store.close()
