"""Causal price-risk extension and its actual manager/persistence boundaries."""
import asyncio
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.trading_runtime.price_confirmed_original_risk import (
    INPUT, RULE, POLICY_KEY, PriceConfirmedOriginalRiskPolicy,
    parse_price_confirmed_original_risk_policy, price_confirmed_original_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.journal_contract import canonical_json


def policy():
    return PriceConfirmedOriginalRiskPolicy((1, 2), (1, 2), 120000, 1000000)


def value(**changes):
    result = FollowThroughFailureInput(10000, 100, 10., 9., 10000, 95000,
        True, .2, .1, 9.5, 9.51, 1000000, 1., False)
    return replace(result, **changes)


def declaration(number=94101):
    from src.trading_runtime import strategy_fifty_seven_release as parent
    from src.trading_runtime.numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
    from src.trading_runtime.declared_early_original_risk_policy import INPUT_CONTRACT
    prior = parent.release_contract()
    draft = replace(prior, number=number, executor_revision=number,
        input_contracts=(*prior.input_contracts, DECLARED_FIXED_ADAPTER, INPUT_CONTRACT, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    policies = {**parent.INHERITED_POLICIES,
        'half_risk_liquidity_policy': parent.HALF_RISK_LIQUIDITY_POLICY,
        'entry_spread_risk_policy': parent.ENTRY_SPREAD_RISK_POLICY_PAYLOAD,
        'early_original_risk_failure_policy': parent.EARLY_FAILURE_POLICY_PAYLOAD,
        POLICY_KEY: json.loads(canonical_json(policy().payload()))}
    return release, policies


def contract():
    from src.trading_runtime.numbered_fixed_strategy import DeclaredFixedStrategyContract
    release, policies = declaration()
    return DeclaredFixedStrategyContract(release.number, release.executor_strategy_id,
        release.evaluation_interval, release, canonical_json(policies))


def test_exact_threshold_accepts_positive_histogram_and_keeps_original_reference():
    witness = price_confirmed_original_risk_failure(value(), policy=policy())
    assert witness.reference_ask == 10. and witness.initial_stop == 9.
    assert witness.macd_line > witness.macd_signal
    assert price_confirmed_original_risk_failure(value(bid=9.5001), policy=policy()) is None
    assert price_confirmed_original_risk_failure(value(completed_five_second_close_int=95001), policy=policy()) is None


@pytest.mark.parametrize('changes', [dict(pending_exit=True), dict(position_quantity=0.),
    dict(completed_five_second_boundary_ms=15000), dict(completed_five_second_boundary_ms=None),
    dict(first_held_boundary_ms=5100), dict(price_valid=False), dict(macd_line=None),
    dict(macd_signal=float('nan')), dict(quote_age_us=1000001), dict(bid=None),
    dict(boundary_ms=10100), dict(boundary_ms=125000, completed_five_second_boundary_ms=125000)])
def test_missing_future_stale_partial_candle_and_late_observations_do_not_fire(changes):
    assert price_confirmed_original_risk_failure(value(**changes), policy=policy()) is None


def test_sessions_window_and_freshness_are_declared():
    ah_only = replace(policy(), premarket_fraction=None, eligibility_ms=9900, quote_max_age_us=10)
    assert price_confirmed_original_risk_failure(value(quote_age_us=10), policy=ah_only) is None
    ah = value(boundary_ms=43210000, first_held_boundary_ms=43200100,
        completed_five_second_boundary_ms=43210000, quote_age_us=10)
    assert price_confirmed_original_risk_failure(ah, policy=ah_only) is not None
    assert price_confirmed_original_risk_failure(replace(ah, quote_age_us=11), policy=ah_only) is None
    assert price_confirmed_original_risk_failure(value(boundary_ms=30000000,
        first_held_boundary_ms=29990100, completed_five_second_boundary_ms=30000000), policy=policy()) is None


@pytest.mark.parametrize('changes', [dict(reference_ask=9.), dict(initial_stop=True),
    dict(position_quantity=-1.), dict(first_held_boundary_ms=10100)])
def test_malformed_ownership_is_rejected(changes):
    with pytest.raises(ValueError):
        price_confirmed_original_risk_failure(value(**changes), policy=policy())


def test_adapter_is_paired_canonical_and_number_independent():
    for number in (94101, 94102):
        release, policies = declaration(number)
        assert parse_price_confirmed_original_risk_policy(release, policies) == policy()
    actual = contract()
    assert actual.price_confirmed_original_risk_policy == policy()
    assert actual.allows_adds is False and actual.allows_completed_30s_trailing is False
    assert actual.early_original_risk_policy is not None
    from src.trading_runtime.strategy_sixty_four_contract import strategy_sixty_four_contract
    assert strategy_sixty_four_contract().price_confirmed_original_risk_policy is None


@pytest.mark.parametrize('field,bad', [('premarket_fraction', [True, 2]),
    ('eligibility_ms', True), ('quote_max_age_us', 1000001), ('reference', 'final_average_fill'),
    ('evidence', 'forming_candle'), ('priority', 'before_inherited_exits')])
def test_foreign_policy_and_price_clock_semantics_fail_closed(field, bad):
    release, policies = declaration()
    policies[POLICY_KEY][field] = bad
    with pytest.raises(ValueError):
        parse_price_confirmed_original_risk_policy(release, policies)


def test_native_persistence_validator_accepts_only_the_selected_extension(monkeypatch):
    from src.trading_runtime import numbered_fixed_strategy as numbered
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    selected = contract()
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy', lambda _: selected)
    monkeypatch.setattr('src.trading_runtime.strategy_followthrough_exit.declared_fixed_rule',
        lambda number, rule: rule in selected.release.rule_set_contracts)
    witness = price_confirmed_original_risk_failure(value(), policy=policy())
    validate_witness(witness, strategy_number=selected.strategy_number)
    with pytest.raises(ValueError):
        validate_witness(replace(witness, bid=9.5001), strategy_number=selected.strategy_number)
    from src.trading_runtime.strategy_sixty_four_contract import strategy_sixty_four_contract
    old = strategy_sixty_four_contract()
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy', lambda _: old)
    with pytest.raises(ValueError):
        validate_witness(witness, strategy_number=old.strategy_number)


def test_actual_manager_waits_for_whole_candle_then_routes_one_native_exit(monkeypatch):
    from src.trading_runtime import numbered_fixed_strategy as numbered
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from test_backtest_strategy_one_management import (
        _Runtime, _Evidence, _proposal, _financial, _evidence, _add_rows,
    )
    selected = contract()
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy', lambda _: selected)
    async def run():
        source, runtime = _Evidence(), _Runtime()
        runtime.config = SimpleNamespace(strategy_revision=selected.strategy_number, anchor_date=date(2026, 8, 18))
        exits = []
        entry_sources = []
        def original_entry(source):
            entry_sources.append(source)
            return SimpleNamespace(intent_id='f1741fc1-2171-4c79-b2b0-abf231b80656')
        runtime._strategy_one_entry_intent = original_entry
        async def submit(financial, witness, entry_id):
            exits.append((financial, witness, entry_id))
        runtime.submit_followthrough_failure = submit
        manager = StrategyOneManagementRunner(runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        proposal = replace(_proposal(), strategy_number=selected.strategy_number)
        await manager.on_entry_proposal(proposal)
        await manager.on_management(_financial(), {}, 30200)
        for boundary in (35000, 40000):
            source.rows[boundary] = replace(_evidence(boundary), bid=9.8, ask=9.81)
            rows = _add_rows(boundary)
            rows[100]['quote_timestamp_us'] = int(market_day_boundary(runtime.config.anchor_date, boundary).timestamp()*1000000)
            rows[5000] = dict(boundary_ms=boundary, price_valid=1, close_int=98000, macd_line=.2, macd_signal=.1)
            await manager.on_management(_financial(), rows, boundary)
            assert len(exits) == int(boundary == 40000)
        assert exits[0][1].reference_ask == proposal.reference_ask
        assert exits[0][1].first_held_boundary_ms == 30200
        assert entry_sources == [proposal]
    asyncio.run(run())


def test_normalized_failure_row_roundtrip_retains_selected_price_rule(monkeypatch):
    from uuid import uuid4
    from src.trading_runtime import numbered_fixed_strategy as numbered, strategy_registry as registry
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure, restore_failure
    from test_backtest_strategy_one_management import _financial
    selected = contract()
    old_release, old_contract = registry.numbered_strategy, numbered.numbered_fixed_strategy
    monkeypatch.setattr(registry, 'numbered_strategy',
        lambda number: selected.release if number == selected.strategy_number else old_release(number))
    monkeypatch.setattr(numbered, 'numbered_fixed_strategy',
        lambda number: selected if number == selected.strategy_number else old_contract(number))
    witness = price_confirmed_original_risk_failure(value(), policy=policy())
    source_id = str(uuid4())
    intent = followthrough_exit_intent(witness, _financial(), session_date=date(2026, 8, 18),
        source_entry_intent_id=source_id, strategy_number=selected.strategy_number)
    row = project_followthrough_failure(witness, intent, source_id, run_id=str(uuid4()),
        batch_id=str(uuid4()), parent_record_id=str(uuid4()), assignment_id='A1',
        strategy_number=selected.strategy_number)
    assert intent.metadata == {} and restore_failure(row) == witness
    with pytest.raises(ValueError):
        restore_failure({**row, 'completed_close_int': 95001})


@pytest.mark.parametrize('relative', [
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/trading_runtime/strategy_followthrough_exit.py',
    'src/backend/backtest_strategy_one_management.py',
])
def test_complete_source_projection_preserves_parent_and_rejects_foreign_delta(relative):
    import ast
    import hashlib
    import subprocess
    from pathlib import Path
    from src.backend.backtest_price_risk_compatibility import (
        restore_price_risk_parent_source, PARENT_CODE_COMMIT,
    )
    root = Path(__file__).resolve().parents[1]
    current = (root/relative).read_text(encoding='utf-8')
    parent = subprocess.check_output(['git', 'show', PARENT_CODE_COMMIT+':'+relative],
                                     cwd=root, text=True)
    canonical = lambda source: hashlib.sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()
    assert canonical(restore_price_risk_parent_source(current, relative)) == canonical(parent)
    foreign = current.replace('price_confirmed_original_risk_policy', 'foreign_price_policy')
    with pytest.raises(ValueError):
        restore_price_risk_parent_source(foreign, relative)
    with pytest.raises(ValueError):
        restore_price_risk_parent_source(current+'\nforeign_retained_source = True\n', relative)
