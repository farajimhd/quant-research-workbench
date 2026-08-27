from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, time


@dataclass(frozen=True, slots=True)
class TrialSpec:
    confirmation_profile: str = "quality_only"
    flow_timeframe: str = "1s"
    vwap_timeframe: str = "5s"
    macd_timeframe: str = "5s"
    reentry_trigger_profile: str = "structure_or_flow"
    reentry_cooldown_ms: int = 5_000
    failed_thesis_ms: int = 60_000
    adverse_qmd_score: float = -0.35
    adverse_qmd_confidence: float = 0.55
    profit_trigger: str = "acceleration_slowdown"
    profit_minimum_gain_pct: float = 0.75
    profit_quantity_fraction: float = 1.0
    profit_volatility_multiple: float = 1.0
    stop_method: str = "hybrid"
    stop_volatility_multiple: float = 1.25
    trailing_activation_gain_pct: float = 0.5
    trailing_distance_volatility_multiple: float = 1.0

    @property
    def trial_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def payload(self) -> dict[str, object]:
        return {"trial_id": self.trial_id, **asdict(self)}


@dataclass(frozen=True, slots=True)
class SearchConfig:
    session_date: date = date(2026, 8, 21)
    tuning_start: time = time(4, 0)
    tuning_end: time = time(7, 30)
    validation_start: time = time(7, 30)
    validation_end: time = time(9, 30)
    initial_cash: float = 10_000.0
    candidate_id: str = "fa6be801-dff6-4729-80b9-566efff2dfce"
    run_plan_id: str = "balanced-replay"
    seed: int = 20260821
    tuning_trials: int = 24
    validation_candidates: int = 5


_SPACE = {
    "confirmation_profile": (
        "quality_only",
        "quality_qmd",
        "quality_qmd_vwap",
        "quality_qmd_macd",
        "quality_qmd_vwap_macd",
    ),
    # A full-session 100ms derived envelope is too large to be a reliable
    # backtest transport and creates a false precision advantage. The exact
    # squeeze occurrence remains event-time; these intervals govern only the
    # subsequent confirmation and management observations.
    # Common declared support across causal structure and the price-volume
    # expansion signal. QMD flow-structure score itself is producer-fixed at
    # 100ms and is not falsely resampled by this parameter.
    "flow_timeframe": ("1s", "10s", "30s"),
    "vwap_timeframe": ("1s", "5s", "10s", "30s"),
    "macd_timeframe": ("1s", "5s", "10s", "30s"),
    "reentry_trigger_profile": (
        "structure_only",
        "structure_or_flow",
        "structure_or_vwap",
        "all_momentum",
    ),
    "reentry_cooldown_ms": (1_000, 3_000, 5_000, 10_000),
    "failed_thesis_ms": (5_000, 10_000, 20_000, 30_000, 60_000),
    "adverse_qmd_score": (-0.2, -0.35, -0.5),
    "adverse_qmd_confidence": (0.45, 0.55, 0.65),
    "profit_trigger": (
        "acceleration_slowdown",
        "favorable_move_pct",
        "volatility_multiple",
    ),
    "profit_minimum_gain_pct": (0.4, 0.6, 0.75, 1.0, 1.5),
    # CPAPI isSingleGroup cannot atomically resize every sibling for a partial
    # pocket. Optimize trigger/timing with the approved full-pocket plus
    # optional-reentry contract instead of sampling unsafe configurations.
    "profit_quantity_fraction": (1.0,),
    "profit_volatility_multiple": (0.75, 1.0, 1.5, 2.0),
    "stop_method": ("structure", "volatility", "hybrid"),
    "stop_volatility_multiple": (0.75, 1.0, 1.25, 1.5, 2.0),
    "trailing_activation_gain_pct": (0.25, 0.5, 0.75, 1.0),
    "trailing_distance_volatility_multiple": (0.5, 0.75, 1.0, 1.5),
}


def generate_trials(config: SearchConfig) -> list[TrialSpec]:
    """Return a deterministic, restart-stable bounded sample including baseline."""

    if config.tuning_trials < 1:
        raise ValueError("tuning_trials must be positive")
    rng = random.Random(config.seed)
    trials = [TrialSpec()]
    seen = {trials[0].trial_id}
    while len(trials) < config.tuning_trials:
        trial = TrialSpec(**{
            key: rng.choice(values)
            for key, values in _SPACE.items()
        })
        if trial.trial_id in seen:
            continue
        seen.add(trial.trial_id)
        trials.append(trial)
    return trials
