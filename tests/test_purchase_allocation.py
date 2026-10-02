"""Shared source capacity, final readback and no-send controls."""
import copy
import json
import os
from unittest.mock import patch

import pytest

from erp_harness.erp.purchase_allocation import (
    PurchaseAllocationError,
    allocate,
    final_purchase_verification,
    inspect_purchase_allocation,
)
from erp_harness.erp.task_evidence import TaskEvidence, failure_result
from tests.test_actions import _actions


def seed(tmp_path, quantities=(7, 12), purchase=10):
    actions, writer, runtime = _actions(path=tmp_path / 'actions.sqlite3')
    client = runtime.client
    client.metadata.update(product_uom_id={'type': 'many2one'}, product_qty={'type': 'float'})
    client.records['sale.order'] = {
        i: {'id': i, 'name': f'SO{i}', 'state': 'sale', 'company_id': [1, 'C'], 'order_line': [i]}
        for i in (7, 8)}
    client.records['sale.order.line'] = {
        i: {'id': i, 'order_id': [i, f'SO{i}'], 'product_id': [1, 'P'],
            'product_uom_qty': q, 'product_uom_id': [1, 'Unit'], 'display_type': False}
        for i, q in zip((7, 8), quantities)}
    client.records['purchase.order'][8].update(origin='SO7', order_line=[18], company_id=[1, 'C'], name='PO8')
    client.records['purchase.order.line'] = {18: {'id': 18, 'order_id': [8, 'PO8'],
        'product_id': [1, 'P'], 'product_qty': purchase, 'product_uom_id': [1, 'Unit'], 'display_type': False,
        'price_unit': 10, 'tax_ids': [], 'date_planned': '2026-10-10'}}
    client.records['product.product'] = {1: {'id': 1, 'name': 'P'}}
    return actions, writer, runtime


@pytest.mark.parametrize('qty,demand', [(10, 7), (6, 4)])
def test_actual_failure_sizes_and_valid_multiple_origins(tmp_path, qty, demand):
    actions, writer, runtime = seed(tmp_path, (demand, 12), qty)
    try:
        result = inspect_purchase_allocation(runtime, [8], [7, 8])
        assert result['status'] == 'failed'
        assert result['checks'][0]['source_capacity'] == demand
        runtime.client.records['purchase.order'][8]['origin'] = 'SO7, SO8'
        result = inspect_purchase_allocation(runtime, [8], [7, 8])
        assert result['status'] == 'passed'
        assert sum(r['quantity'] for r in result['feasible_allocation']) == pytest.approx(qty)
        assert all(r['quantity'] > 0 for r in result['feasible_allocation'])
        assert writer.calls == []
    finally:
        actions.store.close()


def test_flow_checks_shared_capacity_and_allows_different_valid_distributions():
    assert allocate([(1, 7, [7]), (2, 6, [7])], {7: 10}) is None
    assert allocate([(1, 7, [7, 8]), (2, 6, [7])], {7: 7, 8: 7}) is not None
    assert allocate([(1, .5, [7, 8])], {7: .25, 8: .25}) is not None
    assert allocate([(1, .5, [7, 8])], {7: .25, 8: .25}, minimum=1) is None
    assert allocate([(1, 10, [7, 7])], {7: 20}) is None


@pytest.mark.parametrize('change', ['missing_line', 'units', 'company', 'policy'])
def test_unavailable_or_incompatible_evidence_never_passes(tmp_path, change):
    actions, writer, runtime = seed(tmp_path, purchase=5)
    try:
        if change == 'missing_line':
            runtime.client.records['sale.order.line'].pop(7)
        elif change == 'units':
            runtime.client.records['sale.order.line'][7]['product_uom_id'] = [2, 'Box']
        elif change == 'company':
            runtime.client.records['sale.order'][7]['company_id'] = [2, 'Other']
        else:
            class Policy:
                def restricted_fields(self, *args):
                    return {'product_qty'}
            runtime.policy = Policy()
        assert inspect_purchase_allocation(runtime, [8], [7, 8])['status'] == 'unknown'
        assert writer.calls == []
    finally:
        actions.store.close()


def test_host_capacity_blocks_release_and_final_readback_detects_later_change(tmp_path):
    actions, writer, runtime = seed(tmp_path)
    try:
        spec = {'version': 1, 'instruction_sha256': 'confirmed-demand', 'purchase_sources': [{
            'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
            'source': {'model': 'sale.order', 'domain': [['id', 'in', [7, 8]]], 'field': 'name'},
            'check_demand_capacity': True}]}
        actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
        with patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'purchase.order.button_confirm'}):
            bad = actions.execute_method('purchase.order', 'button_confirm', kwargs={'ids': [8]})
            assert not bad['success'] and writer.calls == []
            runtime.client.records['purchase.order'][8]['origin'] = 'SO7, SO8'
            result = actions.execute_method('purchase.order', 'button_confirm', kwargs={'ids': [8]})
            assert result['success'] and result['verification']['evidence']['purchase_sources']
            row = actions.store.get(result['action_id'])
            runtime.client.records['purchase.order'][8]['origin'] = 'SO7'
            with pytest.raises(ValueError, match='quantities are not verified'):
                actions._verify(row, row['result'])
            assert final_purchase_verification(actions)['status'] == 'failed'
            assert final_purchase_verification(actions)['enforced'] is True
            assert len(writer.calls) == 1
    finally:
        actions.store.close()


@pytest.mark.parametrize('quantity,capacity', [(10, 7), (6, 4)])
def test_public_action_rejection_explains_allocation_before_any_write(tmp_path, quantity, capacity):
    actions, writer, runtime = seed(tmp_path, (capacity, 12), quantity)
    try:
        spec = {'version': 1, 'instruction_sha256': 'confirmed-demand', 'purchase_sources': [{
            'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
            'source': {'model': 'sale.order', 'domain': [['id', 'in', [7, 8]]], 'field': 'name'},
            'check_demand_capacity': True}]}
        actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
        with patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'purchase.order.button_confirm'}):
            result = actions.call('execute_method', {
                'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]},
            })
        assert result['success'] is False
        assert result['reason_code'] == result['failure']['code'] == 'purchase_allocation_unverified'
        assert result['failure']['layer'] == result['failure_layer'] == 'business_precondition'
        assert result['next_action'] == 'read_purchase_allocation'
        assert result['failure']['stage'] == 'before_send'
        assert result['failure']['write_dispatch_started'] is False
        assert result['approval_required'] is result['retry_safe'] is False
        assert result['recovery_request'] == {'tool': 'mcp_odoo_read_purchase_allocation', 'arguments': {
            'purchase_ids': [8], 'order_ids': [7, 8], 'instance': 'default',
        }}
        report = result['business_condition']
        assert report['status'] == 'failed'
        assert report['checks'][0] == {'purchase_id': 8, 'quantity': quantity,
            'source_capacity': capacity, 'origins': ['SO7'], 'status': 'failed'}
        assert any(row.get('reason') == 'shared_demand_capacity' and row['status'] == 'failed'
                   for row in report['checks'])
        assert '分配' in result['error']
        assert report == {**inspect_purchase_allocation(runtime, [8], [7, 8], product_id=1), 'instance': 'default'}
        assert writer.calls == [] and actions.store.summary()['actions'] == 0
        assert runtime.client.records['purchase.order'][8]['state'] == 'draft'
    finally:
        actions.store.close()


@pytest.mark.parametrize('base', [ValueError, PurchaseAllocationError])
def test_unknown_value_error_subclass_cannot_publish_a_report_or_body(tmp_path, base):
    class ExternalValueError(base):
        pass

    error = ExternalValueError({'reason': 'private-external-report'}) if base is PurchaseAllocationError \
        else ExternalValueError('private-external-body')
    error.report = {'reason': 'private-external-report'}
    actions, writer, _ = seed(tmp_path)
    try:
        with patch.object(actions, '_prestate', side_effect=error), \
                patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                    'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'purchase.order.button_confirm'}):
            result = actions.call('execute_method', {
                'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]},
            })
        assert result['reason_code'] == 'tool_failed_unknown'
        assert 'business_condition' not in result and 'private-external' not in json.dumps(result)
        assert writer.calls == [] and actions.store.summary()['actions'] == 0
    finally:
        actions.store.close()


def test_allocation_recovery_request_preserves_scoped_instance():
    report = {'status': 'failed', 'scope': {
        'purchase_ids': [8], 'sale_order_ids': [7], 'instance': 'branch',
    }, 'checks': []}
    result = failure_result(PurchaseAllocationError(report))
    assert result['recovery_request'] == {'tool': 'mcp_odoo_read_purchase_allocation', 'arguments': {
        'purchase_ids': [8], 'order_ids': [7], 'instance': 'branch',
    }}


@pytest.mark.parametrize('code,stage', [
    ('action_outcome_unknown', 'send'), ('action_verification_failed', 'verification'),
])
def test_allocation_details_do_not_override_dispatched_action_uncertainty(code, stage):
    report = {'status': 'failed', 'scope': {'purchase_ids': [8], 'sale_order_ids': [7]},
              'checks': [{'purchase_id': 8, 'quantity': 10, 'source_capacity': 7, 'status': 'failed'}]}
    result = failure_result(PurchaseAllocationError(report), code=code, stage=stage,
                            write_dispatch_started=True)
    assert result['failure']['code'] == code and result['failure']['stage'] == stage
    assert result['failure']['write_dispatch_started'] is True
    assert result['reason_code'] == code
    assert result['next_action'] == 'reconcile_without_replay'
    assert result['retry_safe'] is False and 'recovery_request' not in result
    assert result['business_condition'] == report


def test_post_send_allocation_failure_stays_unresolved_and_reconcile_never_resends(tmp_path):
    actions, writer, runtime = seed(tmp_path)
    try:
        runtime.client.records['purchase.order'][8]['origin'] = 'SO7, SO8'
        spec = {'version': 1, 'instruction_sha256': 'confirmed-demand', 'purchase_sources': [{
            'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
            'source': {'model': 'sale.order', 'domain': [['id', 'in', [7, 8]]], 'field': 'name'},
            'check_demand_capacity': True}]}
        actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
        original_send = writer.execute_method

        def send_and_change_allocation(*args, **kwargs):
            result = original_send(*args, **kwargs)
            runtime.client.records['purchase.order'][8]['origin'] = 'SO7'
            return result

        with patch.object(writer, 'execute_method', side_effect=send_and_change_allocation), \
                patch.dict(os.environ, {'ODOO_MCP_ENABLE_WRITES': '1',
                    'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS': 'purchase.order.button_confirm'}):
            result = actions.call('execute_method', {
                'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]},
            })
            assert result['success'] is False and result['action_status'] == 'needs_reconciliation'
            assert result['failure']['code'] == 'action_verification_failed'
            assert result['next_action'] == 'reconcile_without_replay'
            assert result['business_condition']['checks'][0]['source_capacity'] == 7
            action_id = result['action_id']
            repeated = actions.call('execute_method', {
                'model': 'purchase.order', 'method': 'button_confirm', 'kwargs': {'ids': [8]},
            })
            assert repeated['action_id'] == action_id and repeated['action_status'] == 'needs_reconciliation'
            assert repeated['reason_code'] == repeated['failure']['code'] == 'action_verification_failed'
            assert repeated['failure']['write_dispatch_started'] is True
            assert repeated['next_action'] == 'reconcile_without_replay'
            assert 'recovery_request' not in repeated
            reconciled = actions.reconcile(action_id)
            assert reconciled['action_status'] == 'needs_reconciliation'
            assert reconciled['failure']['code'] == 'action_verification_failed'
            assert reconciled['failure']['stage'] == 'verification'
            assert reconciled['failure']['write_dispatch_started'] is True
            assert reconciled['next_action'] == 'reconcile_without_replay'
            assert 'recovery_request' not in reconciled
        assert actions.store.get(action_id)['status'] == 'needs_reconciliation'
        assert len(writer.calls) == 1
    finally:
        actions.store.close()


def test_shared_competing_po_scope(tmp_path):
    actions, _, runtime = seed(tmp_path, purchase=5)
    try:
        runtime.client.records['purchase.order'][9] = {
            **copy.deepcopy(runtime.client.records['purchase.order'][8]), 'id': 9, 'order_line': [19]}
        runtime.client.records['purchase.order.line'][19] = {
            **copy.deepcopy(runtime.client.records['purchase.order.line'][18]), 'id': 19, 'order_id': [9, 'PO9']}
        result = inspect_purchase_allocation(runtime, [8, 9], [7, 8])
        assert result['checks'][0]['status'] == 'passed'
        assert result['status'] == 'failed'
    finally:
        actions.store.close()


def test_host_product_scope_does_not_confuse_other_products(tmp_path):
    actions, _, runtime = seed(tmp_path, purchase=5)
    try:
        other = {**copy.deepcopy(runtime.client.records['purchase.order.line'][18]),
                 'id': 19, 'product_id': [2, 'Other'], 'product_qty': 999}
        runtime.client.records['purchase.order'][8]['order_line'].append(19)
        runtime.client.records['purchase.order.line'][19] = other
        assert inspect_purchase_allocation(runtime, [8], [7, 8])['status'] == 'unknown'
        result = inspect_purchase_allocation(runtime, [8], [7, 8], product_id=1)
        assert result['status'] == 'passed'
        assert result['scope']['product_id'] == 1
        assert all(row['product_id'] == 1 for row in result['feasible_allocation'])
    finally:
        actions.store.close()


def test_capacity_scope_requires_the_actual_source_model(tmp_path):
    actions, writer, _ = seed(tmp_path)
    try:
        spec = {'version': 1, 'instruction_sha256': 'confirmed-demand', 'purchase_sources': [{
            'product': {'model': 'product.product', 'domain': [['id', '=', 1]]},
            'source': {'model': 'res.partner', 'domain': [['id', '=', 7]], 'field': 'name'},
            'check_demand_capacity': True}]}
        with pytest.raises(ValueError, match='sale-name sources'):
            TaskEvidence(actions.reads, spec, tmp_path / 'evidence.json')
        assert writer.calls == []
    finally:
        actions.store.close()


def test_zero_quantity_downpayment_has_no_product_or_unit_demand(tmp_path):
    actions, writer, runtime = seed(tmp_path, purchase=5)
    try:
        client = runtime.client
        client.records['sale.order'][7]['order_line'].append(9)
        # Observed Odoo 19 downpayment line from task 2030: no product or UOM.
        client.records['sale.order.line'][9] = {'id': 9, 'order_id': [7, 'SO7'],
            'product_id': False, 'product_uom_qty': 0.0, 'product_uom_id': False, 'display_type': False}
        assert inspect_purchase_allocation(runtime, [8], [7, 8])['status'] == 'passed'
        client.records['sale.order.line'][9]['product_uom_qty'] = 1
        assert inspect_purchase_allocation(runtime, [8], [7, 8])['status'] == 'unknown'
        client.records['sale.order.line'][9]['product_uom_qty'] = -1
        assert inspect_purchase_allocation(runtime, [8], [7, 8])['status'] == 'unknown'
        assert writer.calls == []
    finally:
        actions.store.close()


def test_final_inspection_runs_without_model_tool_and_does_not_invent_stock_policy(tmp_path):
    actions, writer, runtime = seed(tmp_path)
    try:
        # This fixture's domain matcher uses scalar relation IDs; the inspector accepts either form.
        for row in runtime.client.records['sale.order'].values():
            row['company_id'] = 1
        receipts = [{'kind': 'method', 'status': 'verified', 'identity': actions._identity('default'),
            'payload': {'instance': 'default', 'model': model, 'kwargs': {'ids': ids}}}
            for model, ids in [('sale.order', [7, 8]), ('purchase.order', [8])]]
        with patch('erp_harness.erp.purchase_allocation.ActionStore.read_receipts', return_value=receipts):
            result = final_purchase_verification(actions)
            assert result['status'] == 'failed' and result['enforced'] is False
            with patch('erp_harness.erp.purchase_allocation.ActionStore.read_receipts', return_value=[receipts[1]]):
                assert final_purchase_verification(actions)['status'] == 'failed'
            runtime.client.records['purchase.order'][8]['origin'] = 'SO7, SO8'
            assert final_purchase_verification(actions)['status'] == 'passed'
            receipts[1]['payload']['instance'] = 'other'
            with patch.object(actions, '_identity', return_value=receipts[0]['identity']):
                assert final_purchase_verification(actions)['reason'] == 'multiple_instance_scope'
            receipts[1]['payload']['instance'] = 'default'
            receipts.append({'status': 'needs_reconciliation'})
            assert final_purchase_verification(actions)['reason'] == 'unresolved_write'
        assert writer.calls == []
    finally:
        actions.store.close()
