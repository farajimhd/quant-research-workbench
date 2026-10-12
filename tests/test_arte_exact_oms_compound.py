from dataclasses import replace

import pytest

from src.trading_runtime import rule_decision_writer_preflight as audit
from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, prepare_compound_v4_families, publish_compound_v4,
)
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_oms_projection import oms_group_state_batch, load_latest_committed_oms_groups
from tests.test_arte_oms_exact_prices import prepared
from tests.test_arte_journal_commit_v4 import attached_v4_client


def test_exact_oms_compacts_with_source_intent_and_recovers_one_commit(monkeypatch):
    group, kwargs, price = prepared()
    original = kwargs['published_intent_batch']
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    before = dict(exact.group)
    from src.backend.backtest_typed_publisher import _coalesce_v4_units
    compound, = _coalesce_v4_units((original, exact), max_events=2)
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    base, families = prepare_compound_v4_families(client, compound)
    assert not client.inserts
    names = [name for name, rows in families if rows]
    assert len(set(names)) == len(names)
    assert names.count('trading_oms_group_state_exact_price_v1') == 1
    assert names.count('trading_oms_order_state_exact_price_v1') == 1
    assert dict(exact.group) == before
    assert publish_compound_v4(client, compound) == exact.base.batch_id
    prefix = load_verified_v4_prefix(client, exact.base.run_id)
    assert prefix.batch_ids == (exact.base.batch_id,)
    restored, = load_latest_committed_oms_groups(client, prefix,
        exact_prices=True, require_tactic=True)
    assert restored.orders == tuple(group.orders)
    assert restored.group['current_limit_price'] == price
    assert restored.tactic_recorded
    assert publish_compound_v4(client, compound) == exact.base.batch_id


def test_compound_rejects_replaced_exact_price_child_before_publication(monkeypatch):
    group, kwargs, _ = prepared()
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    compound = coalesce_v4_units((kwargs['published_intent_batch'], exact), max_events=2)
    children = dict(compound.children)
    children['exact_oms_orders'] = ({**children['exact_oms_orders'][0], 'limit_price_bits': 1},)
    broken = replace(compound, children=children)
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    with pytest.raises(ValueError, match='lost normalized'):
        publish_compound_v4(client, broken)
    assert not client.inserts and not client.typed_insert_dispatch.reserved
