import json
from dataclasses import replace

import pytest

from src.trading_runtime import histogram_slope as H
from src.trading_runtime import strategy_engine as S
from tests.test_long_momentum_r3_acceptance import bar
from tests.test_long_momentum_strategy import NOW, assignment


def sample(second, histogram):
    return bar(second=second, close=100, macd_line=.2 + histogram, macd_signal=.2,
               macd_histogram=histogram, position_quantity=10, average_price=100,
               vwap=99)


def test_slope_and_positive_histogram_exit_with_three_bars():
    policy = H.validate_policy({"enabled": True})
    state = {}
    for second, value in enumerate((.03, .04, .03)):
        obs = sample(second, value)
        H.record(state, obs)
        route = H.exit_route(policy, state, obs)
        assert (route is not None) == (second == 2)
    assert route["evidence"]["macd_histogram"] > 0
    assert route["evidence"]["histogram_slope_bps_per_second"] == 0
    assert route["position_fraction"] == 1


def test_positive_tolerance_scale_invariance_and_checkpoint_restore():
    policy = H.validate_policy({"enabled": True, "threshold_bps_per_second": .11})
    for scale in (1, 10):
        state = {}
        for second, h in enumerate((.03, .031, .032)):
            obs = replace(sample(second, h), price=100 * scale,
                          macd_line=(.2 + h) * scale, macd_signal=.2 * scale)
            H.record(state, obs)
        state = json.loads(json.dumps(state))
        route = H.exit_route(policy, state, obs)
        assert route["evidence"]["histogram_slope_bps_per_second"] == pytest.approx(.1)
        assert H.exit_route({**policy, "threshold_bps_per_second": 0}, state, obs) is None


def test_cached_provisional_macd_cannot_replace_completed_values():
    state = {}
    obs = replace(sample(0, .03), source_values={
        "indicator.macd.line@1s": {"value": -100},
        "indicator.macd.signal@1s": {"value": 100},
    })
    H.record(state, obs)
    assert state["completed_histogram_slope_samples"][0][1] == pytest.approx(.03)


def test_completed_macd_dependency_routes_even_with_other_exits_disabled():
    p = S.resolve_long_momentum_parameters({"momentum_management": {
        "histogram_slope_exit": {"enabled": True}, "downside_loss_guard": {"enabled": False}}})
    p["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
    obs = replace(sample(0, .03), changed_source_ids=("indicator.macd.line@1s",))
    assert S._observation_updates_active_rules(p, obs, reentries=0)


def test_ticks_other_timeframes_gaps_duplicates_and_future_samples():
    state = {}
    policy = H.validate_policy({"enabled": True})
    first = sample(0, .03)
    H.record(state, first)
    for obs in (replace(sample(1, -.5), source_timeframe=""),
                replace(sample(1, -.5), source_timeframe="5s"),
                replace(sample(1, -.5), evaluation_events=("market_data_update",)),
                sample(0, -.5), sample(-1, -.5)):
        H.record(state, obs)
    assert len(state["completed_histogram_slope_samples"]) == 1
    H.record(state, sample(2, .02))
    assert len(state["completed_histogram_slope_samples"]) == 1
    H.record(state, sample(3, .01))
    H.record(state, sample(4, .005))
    assert H.exit_route(policy, state, sample(3, .01)) is None
    assert H.exit_route(policy, state, replace(sample(4, .005), source_timeframe="")) is None
    assert H.exit_route(policy, state, sample(4, .005)) is not None
    H.record(state, replace(sample(5, .005), macd_line=float("nan")))
    assert state["completed_histogram_slope_samples"] == []


@pytest.mark.parametrize("policy", [{"window_bars": 1}, {"window_bars": 3.5},
    {"window_bars": 31}, {"enabled": "true"}, {"threshold_bps_per_second": -1},
    {"threshold_bps_per_second": float("nan")}])
def test_invalid_policy_rejected_by_resolver(policy):
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters({"momentum_management": {"histogram_slope_exit": policy}})


def test_real_engine_exit_and_unchanged_baseline():
    for enabled in (False, True):
        p = S.resolve_long_momentum_parameters({"momentum_management": {
            "histogram_slope_exit": {"enabled": enabled}}}, revision=47)
        p["protection"]["trailing"]["enabled"] = False
        p["protection"]["luld_profit_target"]["enabled"] = False
        p["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        current = assignment(strategy_revision=47, parameters=p,
            status=S.AssignmentStatus.MANAGING,
            state={"active_stop": 90, "initial_stop": 90, "entry_at": NOW.isoformat(),
                   "entry_reference_price": 100, "high_water_price": 100})
        engine = S.LongMomentumStrategyEngine(revision=47)
        for second, value in enumerate((.03, .04, .03)):
            result = engine.evaluate(current, sample(second, value))
            current = replace(current, state=result.state, status=result.status)
        exits = [i for i in result.evaluation.intents if i.action == "exit"]
        assert bool(exits) == enabled
        if enabled:
            assert exits[0].metadata["exit_route_id"] == "histogram-slope-exit"
            assert exits[0].metadata["cancel_entry_acquisition"]
