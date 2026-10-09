"""Prepared source contract; no native release is admitted by these fixtures."""
from dataclasses import replace
import pytest

from test_complete_market_window import plans
from src.backend import backtest_complete_market_source as source_module
from src.trading_runtime.complete_market_window_policy import (
    INPUT, RULE, CompleteMarketWindowPolicy, parse_complete_market_window_policy,
    declared_complete_market_window_policy,
)


def policy():
    return CompleteMarketWindowPolicy(1, 25600, 1048576, 1000, 16777216, 2000, 2)


def release():
    from src.trading_runtime.strategy_ninety_seven_release import release_contract
    value = release_contract()
    value = replace(value, number=900010, executor_revision=900010,
                    input_contracts=value.input_contracts + (INPUT,),
                    rule_set_contracts=value.rule_set_contracts + (RULE,), approved_digest='')
    return replace(value, approved_digest=value.digest())


def test_exact_complete_declaration_and_unselected_release():
    assert parse_complete_market_window_policy(policy().payload()) == policy()
    assert declared_complete_market_window_policy(release(), policy().payload()) == policy()
    from src.trading_runtime.strategy_ninety_seven_release import release_contract
    assert declared_complete_market_window_policy(release_contract(), None) is None
    with pytest.raises(ValueError):
        declared_complete_market_window_policy(release_contract(), policy().payload())


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('window_span_ms', 50), ('max_response_rows', 1.0),
    ('max_window_rows', 0), ('read_ahead_groups', 4097),
    ('responses', 'stream until consumer pauses'), ('source', 'flatfiles'),
    ('failure', 'retry partial response'),
])
def test_complete_policy_rejects_type_aliases_and_semantic_drift(field, value):
    declaration = policy().payload()
    declaration[field] = value
    with pytest.raises(ValueError):
        parse_complete_market_window_policy(declaration)


def test_missing_or_duplicate_pair_is_rejected():
    value = release()
    for inputs, rules in ((value.input_contracts[:-1], value.rule_set_contracts),
                          (value.input_contracts + (INPUT,), value.rule_set_contracts)):
        altered = replace(value, input_contracts=inputs, rule_set_contracts=rules, approved_digest='')
        altered = replace(altered, approved_digest=altered.digest())
        with pytest.raises(ValueError):
            declared_complete_market_window_policy(altered, policy().payload())


class Reader:
    def __init__(self):
        self.closes = 0
    def close(self):
        self.closes += 1


def test_source_resumes_exactly_and_closes_its_own_reader_on_early_stop(monkeypatch):
    market, prices = plans()
    reader, calls = Reader(), []
    def windows(scoped, **kwargs):
        calls.append((scoped.tickers, kwargs['after_boundary_ms'], kwargs['through_boundary_ms']))
        for clock in (300, 400, 500, 600):
            yield clock, {100: {'boundary_ms': clock}}
    monkeypatch.setattr(source_module, 'iter_complete_market_windows', windows)
    source = source_module.prepared_complete_market_source(market, prices=prices,
        through_boundary_ms=1000, client_factory=lambda: reader, policy=policy())
    tape = source('AAA', 200)
    assert reader.closes == 0
    assert next(tape)[0] == 300
    assert calls == [(('AAA',), 200, 1000)]
    tape.close()
    assert reader.closes == 1


@pytest.mark.parametrize('ticker,clock', [('BBB', 0), ('AAA', True), ('AAA', 50), ('AAA', 1100)])
def test_source_rejects_foreign_requests_before_opening_clients(ticker, clock):
    market, prices = plans()
    def factory():
        raise AssertionError('Foreign source request opened a client')
    source = source_module.prepared_complete_market_source(market, prices=prices,
        through_boundary_ms=1000, client_factory=factory, policy=policy())
    with pytest.raises(ValueError):
        next(source(ticker, clock))


def test_finished_scope_does_not_open_or_fabricate_rows():
    market, prices = plans()
    def factory():
        raise AssertionError('Finished scope opened a client')
    source = source_module.prepared_complete_market_source(market, prices=prices,
        through_boundary_ms=1000, client_factory=factory, policy=policy())
    assert tuple(source('AAA', 1000)) == ()


def test_original_scheduler_consumes_prepared_source_in_completed_order(monkeypatch):
    from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
    market, prices = plans()
    reader = Reader()
    def windows(*args, **kwargs):
        for clock in (300, 400, 500):
            yield clock, {100: {'boundary_ms': clock, 'ticker': 'AAA',
                               'session_date': market.sessions[0], 'resolution_ms': 100}}
    monkeypatch.setattr(source_module, 'iter_complete_market_windows', windows)
    source = source_module.prepared_complete_market_source(market, prices=prices,
        through_boundary_ms=1000, client_factory=lambda: reader, policy=policy())
    scheduler = StrategyOneBoundaryScheduler(session_date=market.sessions[0],
        candidate_rows=iter(()), active_source=source, start_after_boundary_ms=200)
    try:
        # A scheduler fixture activates the source; it does not issue Portfolio
        # ownership or claim an accepted acquisition.
        scheduler.reconcile_financial_tickers(('AAA',))
        work = []
        while True:
            item = scheduler.pop_next()
            if item is None:
                break
            work.append(item)
        assert [item.boundary_ms for item in work] == [300, 400, 500]
        assert all(item.broker_rows[0][0] == 'AAA' for item in work)
    finally:
        scheduler.close()
    assert reader.closes == 1
