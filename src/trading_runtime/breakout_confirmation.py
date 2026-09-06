"""Causal candle confirmation and resistance-band geometry for momentum."""
from math import isfinite


def band(row):
    try:
        lower = float(row.get("band_lower", row.get("lower")))
        upper = float(row.get("band_upper", row.get("upper")))
        if isfinite(lower) and isfinite(upper) and 0 < lower <= upper:
            return lower, upper
    except (TypeError, ValueError):
        pass
    return None


def entry_boundary(row, policy):
    if policy.get("break_above_upper_bound"):
        bounds = band(row)
        return bounds[1] if bounds else 0.0
    return float(row.get("price") or row.get("upper") or row.get("lower") or 0)


def record_candle(state, observation):
    if observation.source_timeframe != "1s" or "bar_close" not in observation.evaluation_events:
        return
    timestamp = observation.observed_at.timestamp()
    latest = state.get("latest_entry_candle")
    if latest and latest["timestamp"] >= timestamp:
        return
    state["previous_entry_candle"] = latest
    state["latest_entry_candle"] = {"timestamp": timestamp, "high": observation.bar_high,
                                    "open": observation.bar_open, "close": observation.price}


def slope_reentry_confirmation(state, observation):
    completed = observation.source_timeframe == "1s" and "bar_close" in observation.evaluation_events
    candle = state.get("previous_entry_candle" if completed else "latest_entry_candle")
    evidence = {"previous_candle": candle, "price": observation.price, "open": observation.bar_open,
                "observed_at": observation.observed_at.isoformat()}
    if (observation.bar_open is None or not isfinite(observation.bar_open)
            or observation.bar_open <= 0 or observation.price < observation.bar_open):
        return "entry_closed_candle_bearish", evidence
    high = candle.get("high") if candle else None
    age = observation.observed_at.timestamp() - candle["timestamp"] if candle else None
    if high is None or not isfinite(high) or high <= 0 or age is None or not 0 <= age <= 2:
        return "reentry_previous_candle_unavailable", evidence
    if observation.price <= high:
        return "reentry_previous_candle_high_not_broken", evidence
    return "", evidence


def resistance_rejection(state, observation, levels, *, level_ordinal=None):
    """Touch from below, then retreat below the band before any strict break."""
    timestamp = observation.observed_at.timestamp()
    entry_at = state.get("entry_at")
    tracker = state.get("resistance_rejection_state", {})
    if tracker.get("entry_at") != entry_at or tracker.get("level_ordinal") != level_ordinal:
        tracker = {"entry_at": entry_at, "price": state.get("entry_reference_price"), "tests": {},
                   "level_ordinal": level_ordinal}
    if timestamp < tracker.get("timestamp", timestamp):
        return None
    previous = tracker.get("price")
    if level_ordinal is not None:
        if not tracker.get("target_level_id"):
            entry_price = float(state.get("entry_reference_price") or observation.average_price or 0)
            candidates = {}
            for row in levels:
                bounds = band(row)
                identity = str(row.get("unified_level_id") or "")
                price = float(row.get("price") or 0)
                if bounds and identity and isfinite(price) and price > entry_price > 0:
                    candidates[identity] = price
            ordered = sorted(candidates, key=lambda identity: (candidates[identity], identity))
            if len(ordered) >= level_ordinal:
                tracker.update(target_level_id=ordered[level_ordinal - 1],
                               entry_reference_price=entry_price,
                               selected_at=observation.observed_at.isoformat())
        levels = [row for row in levels if str(row.get("unified_level_id") or "") == tracker.get("target_level_id")]
    tests = tracker.get("tests", {})
    current = {}
    rejected = None
    for row in levels:
        bounds = band(row)
        identity = str(row.get("unified_level_id") or "")
        if not bounds or not identity:
            continue
        lower, upper = bounds
        test = tests.get(identity)
        if test and (test["lower"], test["upper"]) != bounds:
            test = None
        if observation.price > upper:
            continue
        if test and observation.price < lower:
            rejected = rejected or {**test, "rejected_at": observation.observed_at.isoformat(),
                                    "rejection_price": observation.price}
            continue
        if test is None and previous is not None and previous < lower <= observation.price <= upper:
            test = {"unified_level_id": identity, "lower": lower, "upper": upper,
                    "touched_at": observation.observed_at.isoformat(), "touch_price": observation.price}
            if level_ordinal is not None:
                test.update(level_ordinal=level_ordinal, entry_reference_price=tracker["entry_reference_price"],
                            selected_at=tracker["selected_at"])
        if test:
            current[identity] = test
    state["resistance_rejection_state"] = {**tracker, "entry_at": entry_at, "timestamp": timestamp,
                                           "price": observation.price, "tests": current}
    if rejected:
        return {"route_id": "resistance-rejection", "name": "Failed resistance retest",
                "mechanism": "resistance_rejection", "position_fraction": 1.0, "evidence": rejected}
    return None
