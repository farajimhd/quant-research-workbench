"""Survivor-only setup geometry from certified, already-visible pivots."""
from dataclasses import dataclass
from math import floor, isfinite
from struct import pack, unpack
from typing import Mapping

from .strategy_one_bos import ConfirmedPivot
from src.market_engine.streaming_level_book import VERSION


@dataclass(frozen=True, slots=True)
class FrozenLadderResistance:
    qualification_boundary_ms: int
    level_id: str
    lower: float
    upper: float
    confirmed_at_ms: int
    vwap_bits: int
    upper_comparison_int: int


def freeze_ladder_resistance(*, active_levels: tuple[Mapping, ...],
                             qualification_boundary_ms: int,
                             qualification_epoch_ms: int,
                             execution_vwap: float) -> FrozenLadderResistance | None:
    """Freeze nearest band whose lower edge is strictly above qualified VWAP.

    Consume the certified plan's active, fresh completed-second lookup. Do not
    select a band based on a later price or future target outcome. Float64
    scaling matches the prepared completed-cross comparison convention.
    """
    if (not isinstance(active_levels, tuple)
            or type(qualification_boundary_ms) is not int
            or not 0 < qualification_boundary_ms <= 57_600_000 or qualification_boundary_ms % 100
            or type(qualification_epoch_ms) is not int or qualification_epoch_ms <= 0
            or type(execution_vwap) is not float or not isfinite(execution_vwap) or execution_vwap <= 0):
        raise ValueError("Invalid ladder resistance qualification")
    identities = set()
    eligible = []
    for row in active_levels:
        if not isinstance(row, Mapping):
            raise ValueError("Ladder resistance requires typed native level geometry")
        identity, lower, upper = row.get('unified_level_id'), row.get('lower'), row.get('upper')
        confirmed = row.get('confirmed_at_ms')
        if (not isinstance(identity, str) or not identity or identity in identities
                or type(lower) not in (float, int) or type(upper) not in (float, int)
                or not isfinite(lower) or not isfinite(upper) or not 0 < lower <= upper
                or row.get('book_version') != VERSION
                or row.get('role') not in {'support', 'resistance', 'transition'}
                or type(confirmed) is not int or not 0 < confirmed <= qualification_epoch_ms):
            raise ValueError("Ladder resistance received malformed or future V7 geometry")
        identities.add(identity)
        if row['role'] == 'resistance' and lower > execution_vwap:
            eligible.append(row)
    selected = min(eligible, key=lambda row: (row['lower'], row['upper'], row['unified_level_id']), default=None)
    if selected is None:
        return None
    scaled = float(selected['upper']) * 10_000
    if not isfinite(scaled) or not 0 < scaled < 2**53:
        raise ValueError("V7 resistance exceeds the exact comparison domain")
    return FrozenLadderResistance(qualification_boundary_ms, selected['unified_level_id'],
        float(selected['lower']), float(selected['upper']), selected['confirmed_at_ms'],
        unpack('<Q', pack('<d', execution_vwap))[0], floor(scaled))


def ladder_resistance_retained(frozen: FrozenLadderResistance,
                               active_levels: tuple[Mapping, ...]) -> bool:
    """Missing or changed selected geometry invalidates, never retargets."""
    if not isinstance(frozen, FrozenLadderResistance) or not isinstance(active_levels, tuple):
        raise ValueError("Ladder resistance retention requires frozen native geometry")
    if any(not isinstance(row, Mapping) for row in active_levels):
        raise ValueError("Ladder resistance retention requires native level rows")
    matches = [row for row in active_levels if row.get('unified_level_id') == frozen.level_id]
    if len(matches) > 1:
        raise ValueError("Ladder resistance identity is duplicated")
    if not matches:
        return False
    row = matches[0]
    return (row.get('role') == 'resistance' and row.get('book_version') == VERSION
            and row.get('lower') == frozen.lower and row.get('upper') == frozen.upper
            and row.get('confirmed_at_ms') == frozen.confirmed_at_ms)


@dataclass(frozen=True, slots=True)
class FrozenLadderStop:
    qualification_boundary_ms: int
    pivot: ConfirmedPivot
    stop_int: int
    buffer_int: int


def freeze_ladder_stop(*, active_pivots: tuple[ConfirmedPivot, ...],
                       qualification_boundary_ms: int, entry_limit_int: int,
                       tick_int: int, buffer_ticks: int = 0) -> FrozenLadderStop | None:
    """Freeze latest confirmed low; missing/wrong-side geometry rejects setup.

    The caller binds coverage-sealed PivotTimeline output at qualification.
    Validity intervals and source attempts remain producer/preflight authority.
    A later source update must not replace this frozen setup's stop. No pivot
    detector, order effect, cash mutation or per-rejected-row state lives here.
    """
    if (not isinstance(active_pivots, tuple)
            or type(qualification_boundary_ms) is not int
            or not 0 < qualification_boundary_ms <= 57_600_000
            or qualification_boundary_ms % 100
            or type(entry_limit_int) is not int or entry_limit_int <= 0
            or type(tick_int) is not int or tick_int <= 0
            or type(buffer_ticks) is not int or buffer_ticks < 0):
        raise ValueError("Invalid frozen ladder stop policy")
    seen = set()
    for pivot in active_pivots:
        if (not isinstance(pivot, ConfirmedPivot) or not pivot.pivot_id
                or pivot.pivot_id in seen or pivot.side not in {"low", "high"}
                or type(pivot.price_int) is not int or pivot.price_int <= 0
                or type(pivot.pivot_boundary_ms) is not int
                or type(pivot.confirmed_boundary_ms) is not int
                or not 0 < pivot.pivot_boundary_ms < pivot.confirmed_boundary_ms
                or pivot.confirmed_boundary_ms > qualification_boundary_ms):
            raise ValueError("Frozen ladder stop received invalid or future pivot")
        seen.add(pivot.pivot_id)
    latest = max((pivot for pivot in active_pivots if pivot.side == "low"),
                 key=lambda p: (p.pivot_boundary_ms, p.confirmed_boundary_ms, p.pivot_id),
                 default=None)
    if latest is None:
        return None
    stop = latest.price_int // tick_int * tick_int - buffer_ticks * tick_int
    if not 0 < stop < entry_limit_int:
        return None
    return FrozenLadderStop(qualification_boundary_ms, latest, stop, buffer_ticks * tick_int)
