"""Position-owned Strategy 1 management on causal completed-bar evidence.

Backtest and a future typed live adapter must use this one numbered rule
implementation. The evidence source supplies completed bars and level inputs;
this module never creates bars, infers intrabucket order, or writes files.
Portfolio/OMS retains sole order authority and confirms protection changes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from src.trading_runtime.strategy_one_management_evidence import (
    StrategyOneManagementEvidence,
)
from src.trading_runtime.strategy_one_add import propose_strategy_one_add
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ResistanceBreak, advance_protection,
    confirm_protection_transition,
)
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
)


class StrategyOneManagementEvidenceSource(Protocol):
    """One causal boundary contract, independent of market transport."""

    async def management_evidence(
        self, ticker: str, resolutions: Mapping[int, Mapping], *,
        boundary_ms: int,
    ) -> StrategyOneManagementEvidence: ...


ManagerKey = tuple[str, str, str]  # account, assignment, ticker


@dataclass(frozen=True, slots=True)
class StrategyOneClosedPosition:
    """Causal high of completed bars strictly after a filled entry bucket."""

    closed_boundary_ms: int
    entry_resistance_id: str
    high_int: int


@dataclass(frozen=True, slots=True)
class StrategyOneManagementState:
    """Typed mutable-state capture; never a JSON/disk checkpoint."""

    boundary_ms: int
    submitted: tuple[tuple[ManagerKey, StrategyOneEntryProposal], ...]
    positions: tuple[tuple[ManagerKey, ProtectionState], ...]
    pending_breaks: tuple[tuple[ManagerKey, tuple[ResistanceBreak, ...]], ...]
    position_highs: tuple[tuple[ManagerKey, int], ...] = ()
    closed_positions: tuple[tuple[ManagerKey, StrategyOneClosedPosition], ...] = ()


class StrategyOneManagementRunner:
    """Keep only active position state; abort on unowned or unconfirmed risk."""

    def __init__(self, *, runtime: Any,
                 evidence: StrategyOneManagementEvidenceSource,
                 tick_for_ticker: Callable[[str], float],
                 max_pending_breaks: int = 256) -> None:
        if (not callable(getattr(runtime, "submit_strategy_one_proposal", None))
                or not callable(getattr(runtime, "submit_strategy_one_add", None))
                or not callable(getattr(runtime, "submit_strategy_one_protection", None))
                or not callable(getattr(evidence, "management_evidence", None))
                or not callable(tick_for_ticker)
                or type(max_pending_breaks) is not int
                or not 1 <= max_pending_breaks <= 65_536):
            raise ValueError("Strategy 1 manager needs bounded OMS and causal inputs")
        self.runtime = runtime
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        self.contract = numbered_fixed_strategy(
            getattr(getattr(runtime, "config", None), "strategy_revision", 1))
        self.evidence = evidence
        self.tick_for_ticker = tick_for_ticker
        self.max_pending_breaks = max_pending_breaks
        self._submitted: dict[tuple[str, str, str], StrategyOneEntryProposal] = {}
        self._positions: dict[tuple[str, str, str], ProtectionState] = {}
        self._pending_breaks: dict[tuple[str, str, str], list[ResistanceBreak]] = {}
        self._position_highs: dict[ManagerKey, int] = {}
        self._closed_positions: dict[ManagerKey, StrategyOneClosedPosition] = {}

    @staticmethod
    def _validate_capture(state: StrategyOneManagementState, *,
                          max_pending_breaks: int) -> None:
        if (not isinstance(state, StrategyOneManagementState)
                or type(state.boundary_ms) is not int
                or not 0 <= state.boundary_ms <= 57_600_000
                or state.boundary_ms % 100):
            raise ValueError("Strategy 1 management capture has no causal boundary")
        keys = {}
        for family in ("submitted", "positions", "pending_breaks",
                       "position_highs", "closed_positions"):
            rows = getattr(state, family)
            identities = [key for key, _ in rows]
            if (any(not isinstance(key, tuple) or len(key) != 3
                           or any(type(part) is not str or not part for part in key)
                           or key[2] != key[2].upper() for key in identities)
                    or identities != sorted(set(identities))):
                raise ValueError("Strategy 1 management capture repeats an identity")
            keys[family] = set(identities)
        if not keys["positions"] <= keys["submitted"] or not keys[
                "pending_breaks"] <= keys["submitted"]:
            raise ValueError("Strategy 1 management state lacks its entry source")
        if keys["position_highs"] != keys["positions"]:
            raise ValueError("Strategy 1 position high lacks its active position")
        for key, proposal in state.submitted:
            if (not isinstance(proposal, StrategyOneEntryProposal)
                    or (proposal.account_id, proposal.assignment_id,
                        proposal.ticker) != key
                    or proposal.boundary_ms > state.boundary_ms):
                raise ValueError("Strategy 1 submitted entry differs from capture")
        for _, position in state.positions:
            if (not isinstance(position, ProtectionState)
                    or position.boundary_ms > state.boundary_ms):
                raise ValueError("Strategy 1 position is ahead of capture")
        for _, breaks in state.pending_breaks:
            if (not isinstance(breaks, tuple) or len(breaks) > max_pending_breaks
                    or any(not isinstance(row, ResistanceBreak)
                           or not isinstance(row.level, Mapping)
                           or set(row.level) != {
                               "unified_level_id", "lower", "upper", "role", "side"}
                           or type(row.level["unified_level_id"]) is not str
                           or not row.level["unified_level_id"]
                           or row.level["role"] != "resistance"
                           or row.level["side"] != "resistance"
                           or type(row.level["lower"]) not in (int, float)
                           or type(row.level["upper"]) not in (int, float)
                           or not isfinite(row.level["lower"])
                           or not isfinite(row.level["upper"])
                           or not 0 < row.level["lower"] <= row.level["upper"]
                           or row.completed_boundary_ms > state.boundary_ms
                           for row in breaks)):
                raise ValueError("Strategy 1 pending break is ahead of capture")
        for _, high_int in state.position_highs:
            if type(high_int) is not int or high_int <= 0:
                raise ValueError("Strategy 1 position high is invalid")
        for _, prior in state.closed_positions:
            if (not isinstance(prior, StrategyOneClosedPosition)
                    or type(prior.closed_boundary_ms) is not int
                    or not 0 < prior.closed_boundary_ms <= state.boundary_ms
                    or prior.closed_boundary_ms % 100
                    or not prior.entry_resistance_id
                    or type(prior.high_int) is not int or prior.high_int <= 0):
                raise ValueError("Strategy 1 closed position witness is invalid")

    def capture_state(self, *, boundary_ms: int) -> StrategyOneManagementState:
        """Capture only position-owned facts at an ordered global boundary."""
        state = StrategyOneManagementState(
            boundary_ms,
            tuple(sorted(self._submitted.items())),
            tuple(sorted(self._positions.items())),
            tuple(sorted((key, tuple(ResistanceBreak(
                row.completed_boundary_ms, MappingProxyType(dict(row.level)))
                for row in value)) for key, value in
                         self._pending_breaks.items())),
            tuple(sorted(self._position_highs.items())),
            tuple(sorted(self._closed_positions.items())),
        )
        self._validate_capture(state, max_pending_breaks=self.max_pending_breaks)
        return state

    def restore_state(self, state: StrategyOneManagementState) -> None:
        """Cold typed restore only; a populated manager cannot be overwritten."""
        if (self._submitted or self._positions or self._pending_breaks
                or self._position_highs or self._closed_positions):
            raise RuntimeError("Strategy 1 manager is already active")
        self._validate_capture(state, max_pending_breaks=self.max_pending_breaks)
        self._submitted = dict(state.submitted)
        self._positions = dict(state.positions)
        self._pending_breaks = {key: [ResistanceBreak(
            row.completed_boundary_ms, MappingProxyType(dict(row.level)))
            for row in rows] for key, rows in state.pending_breaks}
        self._position_highs = dict(state.position_highs)
        self._closed_positions = dict(state.closed_positions)

    def owns_position_source(self, financial: StrategyOneFinancialView) -> bool:
        """Check ownership before cleanup; a same-bucket exit cannot reenter."""
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 ownership needs typed financial state")
        return ((financial.account_id, financial.assignment_id, financial.ticker)
                in self._submitted)

    def last_closed_position(
        self, financial: StrategyOneFinancialView,
    ) -> StrategyOneClosedPosition | None:
        """Expose only a completed, checkpointed prior position to the reducer."""
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 prior position needs typed financial state")
        return self._closed_positions.get((
            financial.account_id, financial.assignment_id, financial.ticker))

    async def on_entry_proposal(self, proposal: StrategyOneEntryProposal) -> None:
        if not isinstance(proposal, StrategyOneEntryProposal):
            raise TypeError("Strategy 1 manager needs a numbered entry proposal")
        if (proposal.strategy_number != self.contract.strategy_number
                or not self.contract.entry_allowed(proposal.boundary_ms)):
            raise ValueError("Numbered entry crossed its strategy/session contract")
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
                source = self._submitted.get(key)
                high_int = self._position_highs.pop(key, None)
                if source is not None and high_int is not None:
                    self._closed_positions[key] = StrategyOneClosedPosition(
                        boundary_ms, source.bos_support_level_id, high_int)
                self._positions.pop(key, None)
                self._pending_breaks.pop(key, None)
                self._submitted.pop(key, None)
            return
        if self.contract.liquidation_due(boundary_ms):
            await self.runtime.submit_numbered_session_exit(financial, resolutions, boundary_ms)
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
            # The fill can occur anywhere inside its aggregate liquidity bar.
            # Do not include that bucket's high in the prior-position witness.
            self._position_highs[key] = round(source.reference_ask * 10_000)
            return
        previous = self._positions[key]
        if boundary_ms <= previous.boundary_ms:
            raise ValueError("Strategy 1 position management clock did not advance")
        current_bar = resolutions.get(100)
        if current_bar is not None and current_bar.get("price_valid") == 1:
            high_int = current_bar.get("high_int")
            if type(high_int) is not int or high_int <= 0:
                raise ValueError("Strategy 1 held bar lacks certified high")
            self._position_highs[key] = max(
                self._position_highs[key], high_int)
        evidence = await self.evidence.management_evidence(
            financial.ticker, resolutions, boundary_ms=boundary_ms)
        if (type(evidence) is not StrategyOneManagementEvidence
                or evidence.ticker != financial.ticker
                or evidence.boundary_ms != boundary_ms):
            raise ValueError("Strategy 1 management evidence crossed its causal boundary")
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
        if (transition.stop_amendment is None
                and transition.target_amendment is None):
            # No broker command exists to acknowledge. Advance the completed
            # causal clock through the same pure confirmation used by the
            # runtime, without an empty OMS coroutine on every 100 ms row.
            confirmed = confirm_protection_transition(
                previous, transition, target_confirmed=False,
                stop_confirmed=False)
        else:
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
        # This 1s boundary and its co-terminating 100ms row are both closed.
        # Resistances become actionable only after protection has been
        # acknowledged. A rejection consumes this crossing, not a future one.
        if (not self.contract.allows_adds
                or financial.pending_entry or financial.current_purchase_groups >= 3
                or not self.contract.entry_allowed(boundary_ms)):
            return
        purchase_ordinal = financial.current_purchase_groups + 1
        for resistance in sorted(
                evidence.breaks,
                key=lambda row: ((float(row.level["lower"])
                                  + float(row.level["upper"])) / 2,
                                 str(row.level["unified_level_id"]))):
            if purchase_ordinal > 3:
                break
            if resistance.level["unified_level_id"] in previous.accepted_ids:
                continue
            add_financial = replace(
                financial, current_purchase_groups=purchase_ordinal - 1)
            proposal = propose_strategy_one_add(
                add_financial, confirmed, resistance, resolutions,
                boundary_ms=boundary_ms, purchase_ordinal=purchase_ordinal,
                fresh_bid=evidence.bid, fresh_ask=evidence.ask,
                prior_accepted_ids=previous.accepted_ids)
            if proposal is None:
                continue
            results = await self.runtime.submit_strategy_one_add(
                replace(proposal, strategy_number=self.contract.strategy_number))
            if (len(results) != 1
                    or results[0].get("order_group") is None
                    or results[0].get("decision", {}).get("status")
                    not in {"approved", "resized"}):
                continue
            purchase_ordinal += 1
