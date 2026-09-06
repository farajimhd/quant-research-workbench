"""Opt-in completed-bar histogram slope exit; no developing MACD samples."""
from math import isfinite
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.trading_runtime.strategy_engine import StrategyObservation


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    result = {"enabled": False, "window_bars": 3, "threshold_bps_per_second": 0.0, **policy}
    if not isinstance(result["enabled"], bool):
        raise ValueError("Histogram slope enabled must be boolean")
    result.setdefault("require_positive_slope_for_same_period_reentry", False)
    if not isinstance(result["require_positive_slope_for_same_period_reentry"], bool):
        raise ValueError("Histogram slope reentry gate must be boolean")
    window = result["window_bars"]
    if isinstance(window, bool) or not isinstance(window, int) or not 2 <= window <= 30:
        raise ValueError("Histogram slope window_bars must be an integer between 2 and 30")
    threshold = float(result["threshold_bps_per_second"])
    if not isfinite(threshold) or threshold < 0:
        raise ValueError("Histogram slope threshold must be finite and nonnegative")
    result["threshold_bps_per_second"] = threshold
    return result


def record(state: dict[str, Any], observation: "StrategyObservation") -> None:
    if observation.source_timeframe != "1s" or "bar_close" not in observation.evaluation_events:
        return
    # The direct fields belong to this completed bar. The source cache can also
    # contain provisional intrabar indicators and is intentionally not sampled.
    line, signal = observation.macd_line, observation.macd_signal
    timestamp = observation.observed_at.timestamp()
    rows = state.get("completed_histogram_slope_samples", [])
    if rows and timestamp <= rows[-1][0]:
        return  # Never revise a consumed close or accept out-of-order delivery.
    if line is None or signal is None or not isfinite(line) or not isfinite(signal):
        state["completed_histogram_slope_samples"] = []
        return
    if rows and abs(timestamp - rows[-1][0] - 1.0) > 1e-6:
        rows = []  # Do not bridge missing bars or sessions with a stale slope.
    state["completed_histogram_slope_samples"] = (rows + [[timestamp, line - signal]])[-30:]


def slope_evidence(
    policy: dict[str, Any], state: dict[str, Any], observation: "StrategyObservation"
) -> dict[str, Any] | None:
    if not policy.get("enabled") or observation.source_timeframe != "1s" or "bar_close" not in observation.evaluation_events:
        return None
    window = policy["window_bars"]
    rows = state.get("completed_histogram_slope_samples", [])[-window:]
    if len(rows) != window or rows[-1][0] != observation.observed_at.timestamp():
        return None
    times = [row[0] - rows[0][0] for row in rows]
    mean_t = sum(times) / window
    mean_h = sum(row[1] for row in rows) / window
    numerator = sum((t - mean_t) * (row[1] - mean_h) for t, row in zip(times, rows))
    slope = numerator / sum((t - mean_t) ** 2 for t in times)
    normalized = slope / observation.price * 10_000
    evidence = {
        "contract": "completed-1s-histogram-ols-v1",
        "window_bars": window,
        "samples": rows,
        "as_of": observation.observed_at.isoformat(),
        "normalization_price": observation.price,
        "histogram_slope": slope,
        "histogram_slope_bps_per_second": normalized,
        "threshold_bps_per_second": policy["threshold_bps_per_second"],
        "macd_histogram": rows[-1][1],
    }
    state["histogram_slope_evidence"] = evidence
    return evidence


def exit_route(
    policy: dict[str, Any], state: dict[str, Any], observation: "StrategyObservation"
) -> dict[str, Any] | None:
    evidence = slope_evidence(policy, state, observation)
    if evidence is None or evidence["histogram_slope_bps_per_second"] > policy["threshold_bps_per_second"]:
        return None
    return {
        "route_id": "histogram-slope-exit",
        "name": "Completed one-second histogram flattening",
        "mechanism": "histogram_slope_exit",
        "position_fraction": 1.0,
        "evidence": evidence,
    }


def update_reentry_period(policy, state, observation, line, signal):
    if not policy.get("require_positive_slope_for_same_period_reentry"):
        return
    # Update even while flat, but only completed bars can supply a slope.
    slope_evidence(policy, state, observation)
    gate = state.get("histogram_slope_reentry_gate")
    if (gate and line is not None and signal is not None
            and isfinite(line) and isfinite(signal) and line <= signal
            and observation.observed_at.timestamp() >= gate["exit_timestamp"]):
        state.pop("histogram_slope_reentry_gate", None)


def arm_after_exit(policy, state, observation, reason):
    state.pop("histogram_slope_reentry_gate", None)
    if (policy.get("enabled") and policy.get("require_positive_slope_for_same_period_reentry")
            and reason == "histogram_slope_exit"
            and state.get("histogram_slope_evidence", {}).get("macd_histogram", 0) > 0):
        state["histogram_slope_reentry_gate"] = {
            "exit_at": observation.observed_at.isoformat(),
            "exit_timestamp": observation.observed_at.timestamp(),
        }


def reentry_blocked(policy, state, observation):
    if not (policy.get("enabled") and policy.get("require_positive_slope_for_same_period_reentry")
            and state.get("histogram_slope_reentry_gate")):
        return False
    rows = state.get("completed_histogram_slope_samples", [])
    evidence = state.get("histogram_slope_evidence", {})
    samples = evidence.get("samples", [])
    fresh = (len(rows) >= policy["window_bars"] and samples
             and rows[-1][0] == samples[-1][0]
             and 0 <= observation.observed_at.timestamp() - rows[-1][0] <= 2.0)
    # Do not unlatch on the first positive sample: if it falls again before an
    # otherwise eligible entry, that entry must still satisfy the slope gate.
    return not (fresh and evidence.get("histogram_slope_bps_per_second", 0) > 0)
