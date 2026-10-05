"""Survivor-only setup geometry from certified, already-visible pivots."""
from dataclasses import dataclass

from .strategy_one_bos import ConfirmedPivot


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
