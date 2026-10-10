"""Declared contracts and real compiled-pair checkpoint staging, not finance."""
import asyncio
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from src.trading_runtime import numbered_fixed_strategy as numbered
from src.trading_runtime.consecutive_price_confirmed_risk import (
    INPUT, RULE, POLICY_KEY, ConsecutivePriceRiskPolicy, consecutive_price_risk_failure,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.price_confirmed_original_risk import (
    INPUT as SINGLE_INPUT, RULE as SINGLE_RULE, POLICY_KEY as SINGLE_KEY,
    PriceConfirmedOriginalRiskPolicy,
)
from test_price_confirmed_original_risk import declaration


def contract():
    release, policies = declaration(number=94201)
    release = replace(release,
        input_contracts=tuple(x for x in release.input_contracts if x != SINGLE_INPUT) + (INPUT,),
        rule_set_contracts=tuple(x for x in release.rule_set_contracts if x != SINGLE_RULE) + (RULE,),
        approved_digest='')
    release = replace(release, approved_digest=release.digest())
    del policies[SINGLE_KEY]
    policy = ConsecutivePriceRiskPolicy(PriceConfirmedOriginalRiskPolicy((1, 2), (1, 2), 120000, 1000000))
    policies[POLICY_KEY] = json.loads(canonical_json(policy.payload()))
    return numbered.DeclaredFixedStrategyContract(release.number, release.executor_strategy_id,
        release.evaluation_interval, release, canonical_json(policies))


def test_declared_contract_selects_diagnostic_policy_without_single_candle_extension():
    selected = contract()
    assert type(selected.confirmed_original_risk_policy) is ConsecutivePriceRiskPolicy
    assert selected.price_confirmed_original_risk_policy is None
    assert selected.premarket_confirmed_original_risk_policy is None
    assert selected.allows_session_exit and selected.allows_followthrough_failure_exit
    assert selected.entry_spread_risk_policy is not None
    assert selected.early_original_risk_policy is not None


def test_new_and_old_diagnostic_declarations_cannot_coexist():
    selected = contract()
    from src.trading_runtime.confirmed_original_risk_failure import (
        ConfirmedOriginalRiskPolicy, CONFIRMED_ORIGINAL_RISK_INPUT, CONFIRMED_ORIGINAL_RISK_RULE,
    )
    release = replace(selected.release,
        input_contracts=(*selected.release.input_contracts, CONFIRMED_ORIGINAL_RISK_INPUT),
        rule_set_contracts=(*selected.release.rule_set_contracts, CONFIRMED_ORIGINAL_RISK_RULE),
        approved_digest='')
    release = replace(release, approved_digest=release.digest())
    policies = json.loads(selected.policy_json)
    policies['confirmed_original_risk_policy'] = json.loads(canonical_json(ConfirmedOriginalRiskPolicy().payload()))
    with pytest.raises(ValueError, match='exclusive'):
        replace(selected, release=release, policy_json=canonical_json(policies))


def test_selected_schema_and_runner_profile_keep_the_existing_exclusive_lane():
    from src.trading_runtime.original_risk_diagnostic_profile import validate_original_risk_profile
    from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
    from src.trading_runtime.arte_typed_insert_dispatch import _manager_tables
    policy = contract().confirmed_original_risk_policy
    validate_original_risk_profile(False, False, policy)
    assert _manager_tables(policy) == _manager_tables(ConfirmedOriginalRiskPolicy())
    for flags in ((True, False), (False, True)):
        with pytest.raises(ValueError):
            validate_original_risk_profile(*flags, policy)


def test_compiled_pair_stages_exact_pending_checkpoint_before_order_submission(monkeypatch):
    from test_confirmed_original_risk_source import plan, frame
    from test_backtest_strategy_one_management import _Runtime, _Evidence, _financial
    from src.backend.backtest_confirmed_original_risk_source import CompiledCompletedRiskLookup
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
    selected = contract()
    original = numbered.numbered_fixed_strategy
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy',
        lambda number: selected if number == selected.strategy_number else original(number))
    market = plan()
    source = frame((10000, 15000)).with_columns(pl.lit(95000).alias('close_int'),
        pl.lit(.2).alias('macd_line'), pl.lit(.1).alias('macd_signal'))
    lookup = CompiledCompletedRiskLookup(source, plan=market, session_date=date(2026, 1, 1),
        policy=selected.confirmed_original_risk_policy)
    runtime = _Runtime()
    runtime.config = SimpleNamespace(strategy_revision=selected.strategy_number, anchor_date=date(2026, 1, 1))
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=_Evidence(), tick_for_ticker=lambda _: .01)
    manager.bind_completed_risk_lookup(lookup, market)
    prior, newest = lookup.pair_at('TEST', 15000)
    value = FollowThroughFailureInput(15000, 100, 10., 9., 15000, 95000,
        True, .2, .1, 9.5, 9.51, 100, 1., False)
    confirmation = consecutive_price_risk_failure(value, prior=prior, newest=newest,
        policy=selected.confirmed_original_risk_policy)
    entry_id = '11111111-1111-1111-1111-111111111111'
    asyncio.run(manager._submit_followthrough_with_diagnostic(
        replace(_financial(), ticker='TEST'), confirmation.current, entry_id, confirmed=confirmation))
    requests = manager.original_risk_requests(boundary_ms=15000)
    assert len(requests) == 1 and requests[0].source_entry_intent_id == entry_id
    assert requests[0].diagnostic.prior == prior
    assert requests[0].diagnostic.newest == newest
    assert requests[0].diagnostic.semantic_rule == RULE
    assert runtime.calls == []
    with pytest.raises(RuntimeError, match='fenced'):
        manager.original_risk_requests(boundary_ms=20000)


def test_arrow_loader_preserves_declared_filter_and_legacy_default():
    from test_confirmed_original_risk_source import plan, frame
    from src.backend.backtest_confirmed_original_risk_source import load_completed_risk_lookup
    source = frame((10000, 15000)).with_columns(pl.lit(.2).alias('macd_line'),
        pl.lit(.1).alias('macd_signal'))
    class Reader:
        def __init__(self):
            self.queries = []
        def iter_arrow_record_batches(self, query):
            self.queries.append(query)
            assert query.startswith('SELECT ')
            return iter(source.to_arrow().to_batches())
    reader = Reader()
    args = dict(plan=plan(), session_date=date(2026, 1, 1), tickers=('TEST',),
        through_boundary_ms=15000)
    selected = load_completed_risk_lookup(reader, **args,
        policy=contract().confirmed_original_risk_policy)
    legacy = load_completed_risk_lookup(reader, **args)
    assert selected.pair_at('TEST', 15000) is not None
    assert legacy.pair_at('TEST', 15000) is None
    assert selected.bucket_at('TEST', 15000) == legacy.bucket_at('TEST', 15000)
    assert len(reader.queries) == 2
    for foreign in (True, object()):
        with pytest.raises(ValueError):
            load_completed_risk_lookup(reader, **args, policy=foreign)
    assert len(reader.queries) == 2


def test_manager_rejects_lookup_prepared_for_a_different_rule(monkeypatch):
    from test_confirmed_original_risk_source import plan, frame
    from test_backtest_strategy_one_management import _Runtime, _Evidence
    from src.backend.backtest_confirmed_original_risk_source import CompiledCompletedRiskLookup
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    selected = contract()
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy', lambda _: selected)
    runtime = _Runtime()
    runtime.config = SimpleNamespace(strategy_revision=selected.strategy_number, anchor_date=date(2026, 1, 1))
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=_Evidence(), tick_for_ticker=lambda _: .01)
    legacy = CompiledCompletedRiskLookup(frame(), plan=plan(), session_date=date(2026, 1, 1))
    with pytest.raises(ValueError, match='exact declared prepared source'):
        manager.bind_completed_risk_lookup(legacy, plan())
