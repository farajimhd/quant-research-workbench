"""Opt-in completed-bar histogram slope exit; no developing MACD samples."""
from math import isfinite
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.trading_runtime.strategy_engine import StrategyObservation


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    result = {"enabled": False, "window_bars": 3, "threshold_bps_per_second": 0.0, **policy}
    if not isinstance(result["enabled"], bool):
        raise ValueError("Histogram slope enabled must be boolean")
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


def exit_route(
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
    if normalized > policy["threshold_bps_per_second"]:
        return None
    return {
        "route_id": "histogram-slope-exit",
        "name": "Completed one-second histogram flattening",
        "mechanism": "histogram_slope_exit",
        "position_fraction": 1.0,
        "evidence": evidence,
    }
