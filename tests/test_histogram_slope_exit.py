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
            "histogram_slope_exit": {"enabled": enabled, "require_positive_slope_for_same_period_reentry": True}}}, revision=47)
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
            assert result.state["histogram_slope_reentry_gate"]


def gated_policy():
    return H.validate_policy({"enabled": True, "require_positive_slope_for_same_period_reentry": True})


def test_gate_tracks_current_slope_until_entry_or_macd_period_close():
    p = gated_policy()
    state = {}
    for second, h in enumerate((.04, .03, .02)):
        obs = sample(second, h)
        H.record(state, obs)
        H.update_reentry_period(p, state, obs, obs.macd_line, obs.macd_signal)
    H.arm_after_exit(p, state, obs, "histogram_slope_exit")
    state = json.loads(json.dumps(state))
    assert H.reentry_blocked(p, state, obs)
    for second, h, blocked in ((3, .025, True), (4, .03, False), (5, .025, True)):
        obs = sample(second, h)
        H.record(state, obs)
        H.update_reentry_period(p, state, obs, obs.macd_line, obs.macd_signal)
        assert bool(H.reentry_blocked(p, state, obs)) == blocked
    H.update_reentry_period(p, state, sample(6, 0), .2, .2)
    assert not H.reentry_blocked(p, state, sample(7, .01))


def test_gate_requires_fresh_completed_slope_and_other_exits_do_not_arm():
    p = gated_policy()
    state = {}
    for second, h in enumerate((.01, .02, .03)):
        obs = sample(second, h)
        H.record(state, obs)
        H.update_reentry_period(p, state, obs, obs.macd_line, obs.macd_signal)
    H.arm_after_exit(p, state, obs, "histogram_slope_exit")
    assert not H.reentry_blocked(p, state, obs)
    assert H.reentry_blocked(p, state, sample(5, .1))
    assert H.reentry_blocked(p, state, sample(1, .1))
    tick = replace(sample(6, .1), source_timeframe="", evaluation_events=("market_data_update",))
    H.record(state, tick)
    H.update_reentry_period(p, state, tick, .3, .2)
    assert H.reentry_blocked(p, state, tick)
    H.arm_after_exit(p, state, tick, "protective_stop")
    assert not H.reentry_blocked(p, state, tick)


@pytest.mark.parametrize("gate,scores,enters", [(False, (.04,.03,.02), True),
    (True, (.04,.03,.02), False), (True, (.02,.03,.02), False),
    (True, (.02,.03,.04), True)])
def test_real_engine_gates_only_slope_reentry(gate, scores, enters):
    from tests.test_long_momentum_r3_acceptance import parameters
    p = S.resolve_long_momentum_parameters(parameters(), revision=47)
    p["momentum_management"]["histogram_slope_exit"] = gated_policy()
    sources = {"indicator.flow_structure.score@100ms": {"value": .7},
               "indicator.flow_structure.confidence@100ms": {"value": .8},
               "indicator.macd.line@5s": {"value": .4},
               "indicator.macd.signal@5s": {"value": .2},
               "indicator.macd.histogram@5s": {"value": .2}}
    state = {}
    for second, h in enumerate(scores[:2]):
        H.record(state, sample(second, h))
    if gate:
        state["histogram_slope_reentry_gate"] = {"exit_timestamp": NOW.timestamp()-10}
    obs = bar(2, macd_line=.2+scores[2], macd_signal=.2,
              macd_histogram=scores[2], source_values=sources)
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), obs)
    assert any(i.action == "enter_long" for i in result.evaluation.intents) == enters
    if not enters:
        assert result.evaluation.signals[0].reason == "waiting_for_positive_histogram_slope"
    else:
        assert not result.state.get("histogram_slope_reentry_gate")


def test_existing_candidate_is_ungated_and_pending_exit_still_wins():
    assert not H.validate_policy({"enabled": True})["require_positive_slope_for_same_period_reentry"]
    state = {"histogram_slope_reentry_gate": {"exit_timestamp": NOW.timestamp()}}
    assert not H.reentry_blocked(H.validate_policy({"enabled": True}), state, sample(1, .01))
    p = S.resolve_long_momentum_parameters({"momentum_management": {"histogram_slope_exit": gated_policy()}})
    obs = replace(sample(1, .01), position_quantity=0, pending_exit_quantity=10)
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), obs)
    assert result.evaluation.signals[0].reason == "exit_fill_pending"
