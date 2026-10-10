"""Strict declared policy and rejection boundaries; native wiring is separate."""
from types import SimpleNamespace

import pytest

from src.trading_runtime.original_risk_verification_reuse import (
    RULE, POLICY_KEY, OriginalRiskVerificationReusePolicy,
    parse_original_risk_verification_reuse_policy, exclusive_writer_frontier_fence,
)


def test_declared_limits_round_trip_and_unselected_legacy():
    policy = OriginalRiskVerificationReusePolicy(96, 1048576)
    release = SimpleNamespace(rule_set_contracts=(RULE,))
    assert parse_original_risk_verification_reuse_policy(release, {POLICY_KEY: policy.payload()}) == policy
    assert parse_original_risk_verification_reuse_policy(SimpleNamespace(rule_set_contracts=()), {}) is None


@pytest.mark.parametrize('requests,bytes_', [(True, 1), (1, False), (0, 1), (4097, 1),
                                           (1, 0), (1, 16777217), ('96', 1)])
def test_non_integer_or_unbounded_limits_rejected(requests, bytes_):
    with pytest.raises(ValueError, match='bounded integer'):
        OriginalRiskVerificationReusePolicy(requests, bytes_)


@pytest.mark.parametrize('rules,supplied', [((), True), ((RULE,), False), ((RULE, RULE), True)])
def test_rule_policy_pairing_is_exclusive(rules, supplied):
    policies = {POLICY_KEY: OriginalRiskVerificationReusePolicy(96, 1048576).payload()} if supplied else {}
    with pytest.raises(ValueError, match='one declared rule'):
        parse_original_risk_verification_reuse_policy(SimpleNamespace(rule_set_contracts=rules), policies)


@pytest.mark.parametrize('changes', [dict(lifetime='process'), dict(overflow='skip_rows'),
                                    dict(failures='reusable'), dict(extra=True)])
def test_semantic_changes_rejected(changes):
    payload = dict(OriginalRiskVerificationReusePolicy(96, 1048576).payload(), **changes)
    with pytest.raises(ValueError, match='semantics differ'):
        parse_original_risk_verification_reuse_policy(SimpleNamespace(rule_set_contracts=(RULE,)),
                                                       {POLICY_KEY: payload})


def test_caller_claimed_price_source_cannot_issue_writer_fence():
    with pytest.raises(ValueError, match='exact certified price authority'):
        exclusive_writer_frontier_fence(SimpleNamespace(), SimpleNamespace(run_id='claimed'))


@pytest.fixture
def fenced(monkeypatch):
    from hashlib import sha256
    from test_backtest_strategy_first_price_source import authority, Bars
    from test_backtest_v4_keeper_lease import _session, RUN_ID
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import (
        CertifiedPriceReadbackAuthority, compile_certified_price_break_plan)
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.journal_contract import canonical_json
    market, parent = authority()
    source = CertifiedPriceReadbackAuthority(RUN_ID,
        compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars())))
    session = _session()
    lease = BacktestV4KeeperLease.acquire(session, run_id=RUN_ID, owner_id='reuse-test')
    dispatch = TypedInsertDispatch(session.client)
    row = dict(run_id=RUN_ID, batch_id='22222222-2222-4222-8222-222222222222',
               last_sequence=10, status='running')
    gate = SimpleNamespace(mode='open', inflight=(), registered=(),
        active_batch_id='00000000-0000-0000-0000-000000000000', compacted_through=10,
        compacted_batch_id=row['batch_id'],
        compacted_commit_hash=sha256(canonical_json(row).encode()).hexdigest())
    monkeypatch.setattr(dispatch, '_read_gate', lambda run: (gate, None))
    monkeypatch.setattr(writer, '_rows', lambda client, sql: [dict(row)])
    client = SimpleNamespace(backtest_v4_lease=lease, typed_insert_dispatch=dispatch,
                             typed_insert_strict=True)
    yield client, source, row, gate
    lease.release()


def test_real_typed_source_and_keeper_lease_bind_frontier(fenced):
    client, source, _, gate = fenced
    result = exclusive_writer_frontier_fence(client, source)
    assert result['run_id'] == source.run_id
    assert result['price_identity'] == id(source)
    assert result['commit_hash'] == gate.compacted_commit_hash


@pytest.mark.parametrize('field,value', [('mode', 'pending'), ('inflight', ('pending',)),
    ('registered', ('pending',)), ('active_batch_id', '22222222-2222-4222-8222-222222222222'),
    ('compacted_through', 0)])
def test_pending_or_uncompacted_gate_cannot_issue_fence(fenced, field, value):
    client, source, _, gate = fenced
    setattr(gate, field, value)
    with pytest.raises(RuntimeError, match='compacted open gate'):
        exclusive_writer_frontier_fence(client, source)


def test_commit_mutation_rejected(fenced):
    client, source, row, _ = fenced
    row['last_sequence'] += 1
    with pytest.raises(RuntimeError, match='immutable commit metadata'):
        exclusive_writer_frontier_fence(client, source)


@pytest.mark.parametrize('status,accepted', [('completed', True), ('failed', False)])
def test_terminal_frontier_keeps_original_writer_and_exact_status(fenced, status, accepted):
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    client, source, row, gate = fenced
    row['status'] = status
    gate.compacted_commit_hash = sha256(canonical_json(row).encode()).hexdigest()
    if accepted:
        assert exclusive_writer_frontier_fence(client, source)['status'] == status
    else:
        with pytest.raises(RuntimeError, match='immutable commit metadata'):
            exclusive_writer_frontier_fence(client, source)


def test_lost_original_lease_rejected(fenced):
    client, source, _, _ = fenced
    assert client.backtest_v4_lease.release()
    with pytest.raises(RuntimeError, match='lease lost'):
        exclusive_writer_frontier_fence(client, source)


@pytest.mark.parametrize('status', ['failed', 'stopped'])
def test_exact_unsuccessful_terminal_declines_optional_reuse(fenced, status):
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    client, source, row, gate = fenced
    row['status'] = status
    gate.compacted_commit_hash = sha256(canonical_json(row).encode()).hexdigest()
    assert exclusive_writer_frontier_fence(client, source, allow_unavailable=True) is None
    row['last_sequence'] += 1
    with pytest.raises(RuntimeError, match='immutable commit metadata'):
        exclusive_writer_frontier_fence(client, source, allow_unavailable=True)


def test_foreign_dispatch_keeper_rejected(fenced):
    client, source, _, _ = fenced
    client.typed_insert_dispatch.keeper = object()
    with pytest.raises(RuntimeError, match='original exclusive writer'):
        exclusive_writer_frontier_fence(client, source)


def declared_contract(*, rule=True, companion=True):
    import json
    from dataclasses import replace
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.journal_contract import canonical_json
    initialize_numbered_fixed_strategies()
    parent = numbered_fixed_strategy(115)
    release = replace(parent.release, rule_set_contracts=(
        *parent.release.rule_set_contracts, *((RULE,) if rule else ())), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    payload = json.loads(parent.policy_json)
    if companion:
        payload[POLICY_KEY] = OriginalRiskVerificationReusePolicy(96, 1048576).payload()
    return parent, replace(parent, release=release, policy_json=canonical_json(payload))


def test_actual_declared_adapter_exposes_limits_without_changing_risk_rules():
    parent, child = declared_contract()
    assert parent.original_risk_verification_reuse_policy is None
    assert child.original_risk_verification_reuse_policy == OriginalRiskVerificationReusePolicy(96, 1048576)
    assert child.confirmed_original_risk_policy == parent.confirmed_original_risk_policy
    assert child.entry_spread_risk_policy == parent.entry_spread_risk_policy


@pytest.mark.parametrize('rule,companion', [(True, False), (False, True)])
def test_actual_declared_adapter_rejects_partial_selection(rule, companion):
    with pytest.raises(ValueError, match='one declared rule'):
        declared_contract(rule=rule, companion=companion)


def native_hook_fixture(monkeypatch, fenced, *, requests=96, bytes_=1048576):
    from src.trading_runtime import original_risk_verification_reuse as reuse
    from src.trading_runtime import arte_journal_commit_v4 as commit
    client, source, _, _ = fenced
    monkeypatch.setattr(reuse, 'selected_policy', lambda client, source:
                        OriginalRiskVerificationReusePolicy(requests, bytes_))
    calls = []
    def complete(**options):
        batch = options['batch_id']
        calls.append((batch, options['max_rows_per_family']))
        if batch == 'root':
            children = [commit.load_verified_commit_v4(client, run_id=source.run_id,
                batch_id='child', first_price_source=source) for _ in range(2)]
            return {'children': children}, ()
        return {'batch_id': batch}, ()
    monkeypatch.setattr(commit, '_load_verified_commit_v4_complete', complete)
    return reuse, commit, client, source, calls


def test_public_native_hook_reuses_only_within_one_original_writer_call(monkeypatch, fenced):
    reuse, commit, client, source, calls = native_hook_fixture(monkeypatch, fenced)
    for _ in range(2):
        result = commit.load_verified_commit_v4(client, run_id=source.run_id,
                                               batch_id='root', first_price_source=source)
        assert result == ({'children': [({'batch_id': 'child'}, ()), ({'batch_id': 'child'}, ())]}, ())
        assert reuse._OPERATION.get() is None
    assert [batch for batch, _ in calls] == ['root', 'child', 'root', 'child']


def test_public_native_hook_cold_reader_retains_complete_checks(monkeypatch, fenced):
    reuse, commit, client, source, calls = native_hook_fixture(monkeypatch, fenced)
    client.backtest_v4_lease = None
    commit.load_verified_commit_v4(client, run_id=source.run_id,
                                  batch_id='root', first_price_source=source)
    assert [batch for batch, _ in calls] == ['root', 'child', 'child']
    assert reuse._OPERATION.get() is None


def test_public_native_hook_budget_overflow_never_skips_child_verifier(monkeypatch, fenced):
    reuse, commit, client, source, calls = native_hook_fixture(monkeypatch, fenced, bytes_=1)
    commit.load_verified_commit_v4(client, run_id=source.run_id,
                                  batch_id='root', first_price_source=source)
    assert [batch for batch, _ in calls] == ['root', 'child', 'child']
    assert reuse._OPERATION.get() is None


def test_public_native_hook_failed_verification_discards_scope(monkeypatch, fenced):
    reuse, commit, client, source, _ = native_hook_fixture(monkeypatch, fenced)
    def failed(**options):
        raise RuntimeError('complete verifier failed')
    monkeypatch.setattr(commit, '_load_verified_commit_v4_complete', failed)
    with pytest.raises(RuntimeError, match='complete verifier failed'):
        commit.load_verified_commit_v4(client, run_id=source.run_id,
                                      batch_id='root', first_price_source=source)
    assert reuse._OPERATION.get() is None


def test_public_native_hook_inflight_writer_uses_full_verifier(monkeypatch, fenced):
    reuse, commit, client, source, calls = native_hook_fixture(monkeypatch, fenced)
    fenced[3].inflight = ('current pending writer unit',)
    commit.load_verified_commit_v4(client, run_id=source.run_id,
                                  batch_id='root', first_price_source=source)
    assert [batch for batch, _ in calls] == ['root', 'child', 'child']
    assert reuse._OPERATION.get() is None


@pytest.mark.parametrize('status', ['failed', 'stopped'])
def test_public_native_hook_unsuccessful_terminal_runs_complete_verifier(monkeypatch, fenced, status):
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    reuse, commit, client, source, calls = native_hook_fixture(monkeypatch, fenced)
    row, gate = fenced[2:]
    row['status'] = status
    gate.compacted_commit_hash = sha256(canonical_json(row).encode()).hexdigest()
    commit.load_verified_commit_v4(client, run_id=source.run_id,
                                  batch_id='root', first_price_source=source)
    assert [batch for batch, _ in calls] == ['root', 'child', 'child']
    assert reuse._OPERATION.get() is None
