"""Shared source capacity, final readback and no-send controls."""
import copy
import os
from unittest.mock import patch

import pytest

from erp_harness.erp.purchase_allocation import (
    allocate,
    final_purchase_verification,
    inspect_purchase_allocation,
)
from erp_harness.erp.task_evidence import TaskEvidence
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
