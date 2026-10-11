"""Certified possible-entry scope for native feature I/O, never admission."""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass

from .backtest_strategy_one_candidate_store import (
    CertifiedCandidatePlan, project_candidate_plan,
)


@dataclass(frozen=True, slots=True)
class EmptyNativeChannelParticipantSession:
    """No feature authority: any held-position lookup fails closed."""
    candidates: CertifiedCandidatePlan
    flat_start_boundary_ms: int
    through_boundary_ms: int

    def __post_init__(self):
        if native_channel_participant_tickers(self.candidates,
                flat_start_boundary_ms=self.flat_start_boundary_ms,
                through_boundary_ms=self.through_boundary_ms):
            raise ValueError('Empty native session contains possible entry participants')

    def sequence_for(self, ticker):
        raise ValueError('Certified empty native session cannot hold a position')


def native_channel_participant_tickers(
    candidates: CertifiedCandidatePlan, *, flat_start_boundary_ms: int,
    through_boundary_ms: int,
) -> tuple[str, ...]:
    """Select possible participants in (flat start, completed run horizon].

    Full certified coverage is checked by the existing prefix projector.
    Searchsorted operates on ordered column arrays without candle row loops.
    This selector does not inspect fills, returns or realized trade outcomes.
    Source feature history and its certification remain unchanged.
    """
    if (type(flat_start_boundary_ms) is not int
            or type(through_boundary_ms) is not int
            or not 0 <= flat_start_boundary_ms < through_boundary_ms
            or flat_start_boundary_ms % 100):
        raise ValueError('Native participant scope requires an aligned flat-start window')
    visible = project_candidate_plan(candidates, through_boundary_ms=through_boundary_ms)
    return tuple(sorted(row.ticker for row in visible.prepared
                        if int(np.searchsorted(row.boundary_ms,
                                               flat_start_boundary_ms, side='right'))
                        < len(row.boundary_ms)))
