from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import time, timedelta, datetime, date
from typing import Any

from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters

from .config import TrialSpec


_CONFIRMATION_GROUPS = {
    "qmd": "qmd-alignment",
    "vwap": "vwap-confirmation",
    "macd": "macd-confirmation",
}
_REENTRY_TRIGGER_IDS = {
    "structure_only": {"initial-entry-opportunity-break-structure"},
    "structure_or_flow": {
        "initial-entry-opportunity-break-structure",
        "initial-entry-opportunity-bullish-choch",
        "initial-entry-opportunity-price-volume-expansion",
    },
    "structure_or_vwap": {
        "initial-entry-opportunity-break-structure",
        "initial-entry-opportunity-break-vwap",
        "initial-entry-opportunity-vwap-transition",
    },
    "all_momentum": {
        "initial-entry-opportunity-break-structure",
        "initial-entry-opportunity-break-vwap",
        "initial-entry-opportunity-bullish-choch",
        "initial-entry-opportunity-price-volume-expansion",
        "initial-entry-opportunity-vwap-transition",
    },
}


def apply_trial(
    configuration: dict[str, Any],
    trial: TrialSpec,
    *,
    fold_end: time,
) -> dict[str, Any]:
    """Create an immutable research revision without weakening liquidity gates."""

    result = deepcopy(configuration)
    payload = result["payload"]
    parameters = deepcopy(payload["strategy"]["parameters"])
    flatten = _clock_minus(fold_end, seconds=10)
    cutoff = _clock_minus(fold_end, seconds=30)
    parameters["strategy_behavior"] = {
        **dict(parameters.get("strategy_behavior") or {}),
        "eligible_sessions": ["premarket"],
        "entry_cutoff_time": cutoff.isoformat(timespec="seconds"),
        "flatten_time": flatten.isoformat(timespec="seconds"),
        "side": "long",
    }

    _configure_initial_entry(parameters, trial)
    _configure_reentry(parameters, trial)
    _configure_exits(parameters, trial)
    _configure_declared_rule_intervals(parameters, trial)
    parameters["reentry"]["cooldown_ms"] = trial.reentry_cooldown_ms
    pocket = parameters["profit_pocket"]
    pocket.update({
        "trigger": trial.profit_trigger,
        "minimum_gain_pct": trial.profit_minimum_gain_pct,
        "quantity_fraction": trial.profit_quantity_fraction,
        "volatility_multiple": trial.profit_volatility_multiple,
    })
    stop = parameters["protection"]["stop"]
    stop.update({
        "method": trial.stop_method,
        "volatility_multiple": trial.stop_volatility_multiple,
    })
    _configure_protection_profile_catalog(parameters, trial)
    trailing = parameters["protection"]["trailing"]
    trailing.update({
        "activation_gain_pct": trial.trailing_activation_gain_pct,
        "distance_volatility_multiple": trial.trailing_distance_volatility_multiple,
    })
    payload["strategy"]["parameters"] = resolve_long_momentum_parameters(parameters)
    _configure_small_cap_universe(result)
    _make_news_context_optional(payload)

    model_serving = payload.setdefault("market_discovery", {}).setdefault(
        "model_serving", {}
    )
    model_serving.setdefault("bar_gpt", {})["enabled"] = False

    campaign = payload.setdefault("campaign_policy", {})
    campaign["reentry_cooldown_ms"] = trial.reentry_cooldown_ms
    oms = payload["oms"]
    oms_settings = dict(oms.get("settings") or oms)
    protection = oms_settings.setdefault("protection", {})
    protection["stop_method"] = trial.stop_method
    protection["volatility_multiple"] = trial.stop_volatility_multiple
    _configure_oms_protection_profiles(oms, trial)
    if "settings" in oms:
        oms["settings"] = oms_settings
    else:
        oms.update(oms_settings)

    result["revision_id"] = f"research-{trial.trial_id}"
    result["label"] = f"Premarket small-cap squeeze optimization {trial.trial_id}"
    result["release_state"] = "test_candidate"
    result["optimization_trial"] = trial.payload()
    result["content_hash"] = _content_hash(
        {
            "configuration_model": result.get("configuration_model"),
            "payload": payload,
        }
    )
    return result


def assert_hard_liquidity_contract(configuration: dict[str, Any]) -> None:
    parameters = configuration["payload"]["strategy"]["parameters"]
    confirmation = dict(parameters["entry_rules"]["confirmation"])
    quality = next(
        (row for row in confirmation.get("rule_sets") or []
         if row.get("rule_set_id") == "strategy-squeeze-volume-spread-quality"),
        None,
    )
    if quality is None:
        raise ValueError("Optimization trial removed the hard liquidity rule set")
    values = {
        str(row.get("condition_id")): row.get("value")
        for row in quality.get("conditions") or []
    }
    required = {
        "squeeze-session-dollar-volume": 500_000.0,
        "squeeze-trade-rate": 1.0,
        "squeeze-relative-liquidity": 50.0,
        "squeeze-volume-attraction": 1.5,
        "squeeze-spread-quality": 50.0,
    }
    if any(float(values.get(key, -1)) != expected for key, expected in required.items()):
        raise ValueError("Optimization trial weakened the hard liquidity thresholds")
    discovery = dict(
        dict(configuration.get("configuration_model") or {}).get("market_discovery") or {}
    )
    watchlist = next(
        (
            row
            for row in discovery.get("watchlists") or []
            if row.get("watchlist_id") == "squeeze-tradable-candidates"
        ),
        None,
    )
    if watchlist is None or "watchlist-small-caps" not in set(
        watchlist.get("inclusion_rule_sets") or []
    ):
        raise ValueError("Optimization trial removed the causal small-cap universe gate")
    small_cap = next(
        (
            row
            for row in discovery.get("rule_sets") or []
            if row.get("rule_set_id") == "watchlist-small-caps"
        ),
        None,
    )
    limits = {
        str(row.get("condition_id")): row.get("value")
        for row in dict(small_cap or {}).get("conditions") or []
    }
    if float(limits.get("small-cap-positive", -1)) != 0.0 or float(
        limits.get("small-cap-maximum", -1)
    ) != 2_000_000_000.0:
        raise ValueError("Optimization trial changed the causal small-cap market-cap band")


def _configure_small_cap_universe(configuration: dict[str, Any]) -> None:
    model = configuration.setdefault("configuration_model", {})
    discovery = model.setdefault("market_discovery", {})
    watchlist = next(
        row
        for row in discovery.get("watchlists") or []
        if row.get("watchlist_id") == "squeeze-tradable-candidates"
    )
    inclusion = list(watchlist.get("inclusion_rule_sets") or [])
    if "watchlist-small-caps" not in inclusion:
        inclusion.append("watchlist-small-caps")
    watchlist["inclusion_rule_sets"] = inclusion
    universe = configuration["payload"].get("universe") or {}
    for snapshot in universe.get("watchlist_snapshots") or []:
        if snapshot.get("watchlist_id") == "squeeze-tradable-candidates":
            snapshot["inclusion_rule_sets"] = list(inclusion)


def _make_news_context_optional(payload: dict[str, Any]) -> None:
    run_plan = payload.get("run_plan") or {}
    for dependency in run_plan.get("observation_dependencies") or []:
        if str(dependency.get("producer") or "") in {"news", "news_gateway"}:
            dependency["required"] = False


def _configure_protection_profile_catalog(
    parameters: dict[str, Any], trial: TrialSpec
) -> None:
    catalog = dict(parameters.get("protection_profile_catalog") or {})
    seen: set[int] = set()
    for profile in catalog.values():
        identity = id(profile)
        if identity in seen:
            continue
        seen.add(identity)
        _configure_protection_profile(profile, trial)


def _configure_oms_protection_profiles(oms: dict[str, Any], trial: TrialSpec) -> None:
    for profile in oms.get("protection_profiles") or []:
        _configure_protection_profile(profile, trial)


def _configure_protection_profile(profile: dict[str, Any], trial: TrialSpec) -> None:
    for raw_slice in profile.get("slices") or []:
        stop = raw_slice.setdefault("stop", {})
        stop["rule_type"] = {
            "structure": "swing_anchored",
            "volatility": "volatility",
            "hybrid": "hybrid",
        }[trial.stop_method]
        stop["volatility_multiple"] = trial.stop_volatility_multiple
        if trial.stop_method == "volatility":
            stop.pop("anchor_source", None)
            stop.pop("anchor_ordinal", None)
            stop.pop("structural_timeframe", None)
        else:
            stop["anchor_source"] = "strategy_swing"
            stop.setdefault("anchor_ordinal", "most_recent")
            stop.setdefault("structural_timeframe", "strategy")


def _configure_initial_entry(parameters: dict[str, Any], trial: TrialSpec) -> None:
    rules = parameters["entry_rules"]
    confirmation = rules["confirmation"]
    quality = next(
        deepcopy(row)
        for row in confirmation.get("rule_sets") or []
        if row.get("rule_set_id") == "strategy-squeeze-volume-spread-quality"
    )
    selected_names = set(trial.confirmation_profile.removeprefix("quality_").split("_"))
    if trial.confirmation_profile == "quality_only":
        selected_names = set()
    legacy = {
        str(row.get("group_id")): deepcopy(row)
        for row in confirmation.get("groups") or []
    }
    selected = [quality]
    for name in ("qmd", "vwap", "macd"):
        if name not in selected_names:
            continue
        row = legacy[_CONFIRMATION_GROUPS[name]]
        row["rule_set_id"] = row.pop("group_id")
        _set_rule_timeframe(row, name, trial)
        selected.append(row)
    confirmation["rule_sets"] = selected
    confirmation["expression"] = _expression(
        "and", [str(row["rule_set_id"]) for row in selected]
    )
    rules["confirmation"] = confirmation


def _configure_reentry(parameters: dict[str, Any], trial: TrialSpec) -> None:
    reentry = parameters["phase_policy"]["reentry"]["rules"]
    trigger = reentry["trigger"]
    allowed = _REENTRY_TRIGGER_IDS[trial.reentry_trigger_profile]
    trigger["rule_sets"] = [
        row for row in trigger.get("rule_sets") or []
        if str(row.get("rule_set_id")) in allowed
    ]
    trigger["expression"] = _expression(
        "or", [str(row["rule_set_id"]) for row in trigger["rule_sets"]]
    )
    confirmation = reentry["confirmation"]
    names = set(trial.confirmation_profile.removeprefix("quality_").split("_"))
    if trial.confirmation_profile == "quality_only":
        # Re-entry still requires current QMD alignment; Watchlist quality remains mandatory.
        names = {"qmd"}
    keep = {_CONFIRMATION_GROUPS[name].replace("qmd-alignment", "initial-entry-confirmation-qmd-alignment")
            for name in names}
    keep.update({
        "initial-entry-confirmation-vwap-confirmation" if name == "vwap" else
        "initial-entry-confirmation-macd-confirmation" if name == "macd" else
        "initial-entry-confirmation-qmd-alignment"
        for name in names
    })
    confirmation["rule_sets"] = [
        row for row in confirmation.get("rule_sets") or []
        if str(row.get("rule_set_id")) in keep
    ]
    for row in confirmation["rule_sets"]:
        identity = str(row.get("rule_set_id") or "")
        _set_modern_rule_timeframe(row, identity, trial)
    confirmation["expression"] = _expression(
        "and", [str(row["rule_set_id"]) for row in confirmation["rule_sets"]]
    )


def _configure_exits(parameters: dict[str, Any], trial: TrialSpec) -> None:
    for route in parameters["phase_policy"]["exit"]["rule_sets"]:
        route_id = str(route.get("rule_set_id") or "")
        if route_id == "failed-entry-thesis":
            route.setdefault("timing", {})["expires_after_ms"] = trial.failed_thesis_ms
        if route_id != "adverse-momentum":
            continue
        for rule_set in dict(route.get("rules") or {}).get("rule_sets") or []:
            for condition in rule_set.get("conditions") or []:
                condition_id = str(condition.get("condition_id") or "")
                if condition_id == "adverse-qmd-score-condition":
                    condition["value"] = trial.adverse_qmd_score
                elif condition_id == "qmd-confidence-condition":
                    condition["value"] = trial.adverse_qmd_confidence
                if "macd" in condition_id:
                    _set_interval(condition, "left", trial.macd_timeframe)
                    if condition.get("right_source_id"):
                        _set_interval(condition, "right", trial.macd_timeframe)
                elif "qmd" in condition_id:
                    _set_interval(condition, "left", "100ms")


def _set_rule_timeframe(row: dict[str, Any], name: str, trial: TrialSpec) -> None:
    value = {
        "qmd": "100ms",
        "vwap": trial.vwap_timeframe,
        "macd": trial.macd_timeframe,
    }[name]
    for condition in row.get("conditions") or []:
        condition["left_timeframe"] = value
        if condition.get("right_source_id"):
            condition["right_timeframe"] = value


def _set_modern_rule_timeframe(row: dict[str, Any], identity: str, trial: TrialSpec) -> None:
    value = (
        trial.macd_timeframe if "macd" in identity
        else trial.vwap_timeframe if "vwap" in identity
        else "100ms"
    )
    for condition in row.get("conditions") or []:
        _set_interval(condition, "left", value)
        if condition.get("right_source_id"):
            _set_interval(condition, "right", value)


def _set_interval(condition: dict[str, Any], side: str, value: str) -> None:
    suffix = "ms" if value.endswith("ms") else "s"
    count = int(value.removesuffix(suffix))
    condition[f"{side}_interval"] = {
        "value": count,
        "unit": "milliseconds" if suffix == "ms" else "seconds",
    }


def _configure_declared_rule_intervals(value: Any, trial: TrialSpec) -> None:
    """Apply trial intervals only where the source catalog declares support."""

    if isinstance(value, list):
        for item in value:
            _configure_declared_rule_intervals(item, trial)
        return
    if not isinstance(value, dict):
        return
    sources = {
        str(value.get("left_source_id") or ""),
        str(value.get("right_source_id") or ""),
    } - {""}
    timeframe = None
    if any(source.startswith("indicator.macd.") for source in sources):
        timeframe = trial.macd_timeframe
    elif any("vwap" in source for source in sources):
        timeframe = trial.vwap_timeframe
    elif any(
        source.startswith("indicator.structure.")
        or source == "signal.price_volume_expansion.score"
        for source in sources
    ):
        timeframe = trial.flow_timeframe
    elif any(
        source.startswith("indicator.flow_structure.")
        or source
        in {
            "signal.flow_price_divergence.score",
            "signal.liquidity_dislocation.score",
        }
        for source in sources
    ):
        timeframe = "100ms"
    if timeframe is not None:
        for side in ("left", "right"):
            if value.get(f"{side}_source_id"):
                _set_interval(value, side, timeframe)
    for item in value.values():
        _configure_declared_rule_intervals(item, trial)


def _expression(operator: str, ids: list[str]) -> dict[str, Any]:
    if not ids:
        raise ValueError("Optimization rule expression cannot be empty")
    return {
        "kind": "operator",
        "operator": operator,
        "children": [{"kind": "rule_set", "rule_set_id": value} for value in ids],
    }


def _clock_minus(value: time, *, seconds: int) -> time:
    anchor = datetime.combine(date(2000, 1, 1), value) - timedelta(seconds=seconds)
    return anchor.time()


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
