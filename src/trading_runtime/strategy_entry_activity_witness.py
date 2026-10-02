"""Prepared causal entry evidence; publication and admission remain separate."""
from dataclasses import dataclass
from datetime import date
import re
from uuid import UUID

from .strategy_entry_activity_fade import (
    EntryActivityCandle, AFTERHOURS_START_MS, PREMARKET_END_MS,
    entry_activity_allowed, entry_activity_policy_payload,
)


@dataclass(frozen=True, slots=True)
class EntryActivityWitness:
    ticker: str
    session_date: str
    boundary_ms: int
    source_build_id: str
    bars_attempt_id: str
    market_plan_token: str
    activity_source_token: str
    candidate_plan_token: str
    entry_plan_token: str
    parent_selection_token: str
    candles: tuple[EntryActivityCandle | None, ...]

    @property
    def history_active(self) -> bool:
        history = entry_activity_policy_payload()['history_activation_ms']
        opening = AFTERHOURS_START_MS if self.boundary_ms >= AFTERHOURS_START_MS else 0
        return self.boundary_ms >= opening + history


def validate_entry_activity_witness(witness):
    if (type(witness) is not EntryActivityWitness
            or type(witness.ticker) is not str or not witness.ticker
            or type(witness.session_date) is not str
            or type(witness.boundary_ms) is not int
            or not ((0 < witness.boundary_ms < PREMARKET_END_MS)
                    or (AFTERHOURS_START_MS < witness.boundary_ms < 57_600_000))
            or witness.boundary_ms % 100 != 0
            or type(witness.candles) is not tuple or len(witness.candles) != 4):
        raise ValueError('Entry activity witness lacks exact causal entry identity')
    try:
        valid_date = date.fromisoformat(witness.session_date).isoformat() == witness.session_date
        valid_attempt = (type(witness.bars_attempt_id) is str
                         and str(UUID(witness.bars_attempt_id)) == witness.bars_attempt_id
                         and UUID(witness.bars_attempt_id).int != 0)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Entry activity witness date or producer attempt is invalid') from None
    if (not valid_date or not valid_attempt
            or any(type(getattr(witness, name)) is not str
                   or not re.fullmatch('[0-9a-f]{64}', getattr(witness, name))
                   for name in ('source_build_id', 'market_plan_token', 'activity_source_token',
                                'candidate_plan_token', 'entry_plan_token', 'parent_selection_token'))):
        raise ValueError('Entry activity witness lacks canonical source seals')
    if ((witness.history_active and any(type(c) is not EntryActivityCandle for c in witness.candles))
            or (not witness.history_active and any(c is not None for c in witness.candles))
            or not entry_activity_allowed(witness.boundary_ms, witness.candles)):
        raise ValueError('Entry activity witness does not authorize the prepared filter')
    return witness
