"""Canonical scalar rows and content seal for Strategy 1 late-HOD context."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Sequence

from .strategy_one_hod import HodObservation


@dataclass(frozen=True, slots=True)
class HodContext:
    boundary_ms: int
    session_open_int: int
    prior_hod_int: int
    late_mode: bool
    gate_level_id: str

    @classmethod
    def from_observation(cls, state: HodObservation) -> HodContext:
        if not isinstance(state, HodObservation):
            raise ValueError("Strategy 1 HOD context needs completed state")
        return cls(state.boundary_ms, state.session_open_int,
                   state.prior_hod_int, state.late_mode,
                   state.gate.unified_level_id if state.gate else "")


def context_content_hash(rows: Sequence[HodContext]) -> str:
    """Reject gaps/duplicates in the caller's ordered candidate projection."""
    digest = sha256(b"strategy-one-hod-context-content-v1\0")
    previous = 0
    for row in rows:
        if (not isinstance(row, HodContext)
                or type(row.boundary_ms) is not int
                or not previous < row.boundary_ms <= 57_600_000
                or row.boundary_ms % 100
                or type(row.session_open_int) is not int
                or type(row.prior_hod_int) is not int
                or not 0 < row.session_open_int <= row.prior_hod_int
                or type(row.late_mode) is not bool
                or not isinstance(row.gate_level_id, str)
                or len(row.gate_level_id) > 256):
            raise ValueError("Strategy 1 HOD context row is invalid or unordered")
        previous = row.boundary_ms
        for value in (row.boundary_ms, row.session_open_int,
                      row.prior_hod_int, int(row.late_mode),
                      row.gate_level_id):
            encoded = str(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return digest.hexdigest()
