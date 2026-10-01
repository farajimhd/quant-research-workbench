"""Typed completed producer observations; scalar adapter never derives MACD."""
from dataclasses import dataclass
from math import isfinite
import re
from uuid import UUID
import numpy as np
from .strategy_rising_momentum_entry import MOMENTUM_RESOLUTIONS_MS, rising_momentum_entry_mask

@dataclass(frozen=True, slots=True)
class CompletedMomentumObservation:
    resolution_ms: int
    current_boundary_ms: int
    prior_boundary_ms: int
    current_line: float | None
    current_signal: float | None
    prior_line: float | None
    prior_signal: float | None

@dataclass(frozen=True, slots=True)
class RisingMomentumWitness:
    ticker: str
    boundary_ms: int
    source_build_id: str
    source_attempt_id: str
    market_plan_token: str
    observations: tuple[CompletedMomentumObservation, ...]


def validate_momentum_witness(witness: RisingMomentumWitness) -> None:
    if (type(witness) is not RisingMomentumWitness or not isinstance(witness.ticker, str)
            or not witness.ticker or witness.ticker != witness.ticker.upper()
            or type(witness.boundary_ms) is not int
            or not 0 < witness.boundary_ms <= 57_600_000 or witness.boundary_ms % 100
            or type(witness.observations) is not tuple or len(witness.observations) != 2
            or type(witness.market_plan_token) is not str
            or re.fullmatch(r"[0-9a-f]{64}", witness.market_plan_token) is None):
        raise ValueError("Rising momentum witness needs exact typed identity and observations")
    # The producer build is a content-addressed 64hex identity; its technical
    # coverage attempt is a separate UUID. Never coerce one into the other.
    if (type(witness.source_build_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", witness.source_build_id) is None):
        raise ValueError("Rising momentum source needs its exact content build hash")
    for value in (witness.source_attempt_id,):
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError("Rising momentum source needs canonical nonzero UUIDs")
    for resolution, observation in zip(MOMENTUM_RESOLUTIONS_MS, witness.observations):
        if (type(observation) is not CompletedMomentumObservation
                or type(observation.resolution_ms) is not int or observation.resolution_ms != resolution
                or any(type(clock) is not int or not 0 <= clock <= 57_600_000 for clock in
                       (observation.current_boundary_ms, observation.prior_boundary_ms))
                or any(value is not None and (type(value) is not float or not isfinite(value)) for value in
                       (observation.current_line, observation.current_signal,
                        observation.prior_line, observation.prior_signal))):
            raise ValueError("Rising momentum observation needs ordered exact resolutions and finite Float64")


def rising_momentum_entry(witness: RisingMomentumWitness) -> bool:
    """Validate scalar authority and reuse the native one-row (1,2) reducer."""
    validate_momentum_witness(witness)
    clocks = tuple(np.asarray([[getattr(o, name) for o in witness.observations]], dtype=np.int64)
                   for name in ("current_boundary_ms", "prior_boundary_ms"))
    values = tuple(np.asarray([[np.nan if getattr(o, name) is None else getattr(o, name)
                               for o in witness.observations]], dtype=np.float64)
                   for name in ("current_line", "current_signal", "prior_line", "prior_signal"))
    return bool(rising_momentum_entry_mask(np.asarray([witness.boundary_ms], dtype=np.int64),
                                          *clocks, *values)[0])


def numbered_momentum_entry(witness: RisingMomentumWitness, strategy_number: int) -> bool:
    """Pin the stronger rule to 17; retain earlier completed-momentum behavior."""
    if type(strategy_number) is not int or strategy_number not in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22):
        raise ValueError("Numbered momentum rule has no installed consumer")
    if strategy_number in (17, 18, 19, 20, 21, 22):
        from .strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry
        return strong_ten_second_momentum_entry(witness)
    return rising_momentum_entry(witness)
