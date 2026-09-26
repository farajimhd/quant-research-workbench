"""Position-owned Strategy 1 management on the certified sparse bar tape.

This callback is for the numbered Backtest coordinator, not a market builder.
It never creates bars, infers intrabucket order, or writes files. Shared
Portfolio/OMS retains sole order authority and confirms protection changes.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Callable, Mapping

from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ResistanceBreak, advance_protection,
)
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
)


class StrategyOneManagementRunner:
    """Keep only active position state; abort on unowned or unconfirmed risk."""

    def __init__(self, *, runtime: Any, evidence: StrategyOneCausalEvidence,
                 tick_for_ticker: Callable[[str], float],
                 max_pending_breaks: int = 256) -> None:
        if (not callable(getattr(runtime, "submit_strategy_one_proposal", None))
                or not callable(getattr(runtime, "submit_strategy_one_protection", None))
                or not isinstance(evidence, StrategyOneCausalEvidence)
                or not callable(tick_for_ticker)
                or type(max_pending_breaks) is not int
                or not 1 <= max_pending_breaks <= 65_536):
            raise ValueError("Strategy 1 manager needs bounded OMS and causal inputs")
        self.runtime = runtime
        self.evidence = evidence
        self.tick_for_ticker = tick_for_ticker
        self.max_pending_breaks = max_pending_breaks
        self._submitted: dict[tuple[str, str, str], StrategyOneEntryProposal] = {}
        self._positions: dict[tuple[str, str, str], ProtectionState] = {}
        self._pending_breaks: dict[tuple[str, str, str], list[ResistanceBreak]] = {}

    async def on_entry_proposal(self, proposal: StrategyOneEntryProposal) -> None:
        if not isinstance(proposal, StrategyOneEntryProposal):
            raise TypeError("Strategy 1 manager needs a numbered entry proposal")
        key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
        if key in self._submitted:
            raise RuntimeError("Strategy 1 assignment already owns an entry")
        results = await self.runtime.submit_strategy_one_proposal(proposal)
        if (len(results) != 1 or results[0].get("order_group") is None
                or results[0].get("decision", {}).get("status")
                not in {"approved", "resized"}):
            return
        self._submitted[key] = proposal

    async def on_management(
        self, financial: StrategyOneFinancialView,
        resolutions: Mapping[int, Mapping], boundary_ms: int,
    ) -> None:
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 management needs typed financial state")
        key = (financial.account_id, financial.assignment_id, financial.ticker)
        if financial.position_quantity <= 0:
            if not financial.pending_entry and not financial.pending_exit:
                self._positions.pop(key, None)
                self._pending_breaks.pop(key, None)
                self._submitted.pop(key, None)
            return
        source = self._submitted.get(key)
        if source is None:
            raise RuntimeError("Strategy 1 position lacks its normalized entry source")
        if key not in self._positions:
            if boundary_ms < source.boundary_ms:
                raise ValueError("Strategy 1 fill precedes its source entry")
            # The first observed held boundary owns the position. A 1s break
            # at that same boundary cannot be ordered after the fill inside
            # its aggregate bucket, so it cannot advance protection yet.
            self._positions[key] = ProtectionState(
                boundary_ms, source.initial_stop, source.initial_target)
            return
        previous = self._positions[key]
        if boundary_ms <= previous.boundary_ms:
            raise ValueError("Strategy 1 position management clock did not advance")
        evidence = await self.evidence.management_evidence(
            financial.ticker, resolutions, boundary_ms=boundary_ms)
        pending = self._pending_breaks.setdefault(key, [])
        # A failed OMS acknowledgement retries the same completed boundary.
        # Preserve witnesses once, not once per retry.
        seen = {(row.completed_boundary_ms,
                 row.level.get("unified_level_id")) for row in pending}
        additions = [row for row in evidence.breaks
                     if (row.completed_boundary_ms,
                         row.level.get("unified_level_id")) not in seen]
        if len(pending) + len(additions) > self.max_pending_breaks:
            raise RuntimeError("Strategy 1 pending resistance witnesses exceed memory bound")
        pending.extend(additions)
        if evidence.bid is None or evidence.ask is None or financial.pending_exit:
            return
        tick = self.tick_for_ticker(financial.ticker)
        if type(tick) not in (int, float) or not isfinite(tick) or tick <= 0:
            raise ValueError("Strategy 1 management lacks a point-in-time tick")
        transition = advance_protection(
            previous, now_ms=boundary_ms, bid=evidence.bid, ask=evidence.ask,
            tick=tick, low_boundary_ms=evidence.low_boundary_ms,
            low_int=evidence.low_int,
            low_price_valid=evidence.low_int is not None,
            low_extremes_valid=evidence.low_int is not None,
            breaks=tuple(pending), overhead_levels=evidence.overhead_levels,
            price_bearing_bar=evidence.price_bearing_bar)
        confirmed = await self.runtime.submit_strategy_one_protection(
            previous, transition, financial, bid=evidence.bid, ask=evidence.ask)
        if not isinstance(confirmed, ProtectionState):
            raise RuntimeError("Strategy 1 OMS did not return confirmed protection")
        if (confirmed.boundary_ms != boundary_ms
                or not previous.accepted_ids <= confirmed.accepted_ids
                or not 0 < confirmed.stop < confirmed.target):
            raise RuntimeError("Strategy 1 OMS returned inconsistent protection")
        self._positions[key] = confirmed
        pending.clear()
