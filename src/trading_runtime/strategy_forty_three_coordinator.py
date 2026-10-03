"""Sequential batch and leg state for the independent Strategy 43 executor.

The adapter owns source certification and durable publication. Portfolio/OMS
own admission, reservations, execution and protection. A submission receipt
is not a fill and a proposed stop is not a confirmed broker-held stop.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .signals import StrategyIntent
from .strategy_forty_three_rules import (
    EntryBatch, EntryFacts, adaptive_stop, entry_geometry, entry_intents,
    incoming_score, propose_batch,
)
from .strategy_forty_three_oms import LegStopAmendment


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    ordinal: int
    intent_id: str
    group_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class LegState:
    ordinal: int
    intent_id: str
    group_id: str
    outcome: str
    first_fill_ms: int = 0
    average_entry: float = 0.
    held_quantity: int = 0
    confirmed_stop: float = 0.
    peak_after_entry: float = 0.
    latest_fill_ms: int = 0
    managed_boundary_ms: int = 0


@dataclass(frozen=True, slots=True)
class BatchState:
    session_date: date
    batch: EntryBatch
    legs: tuple[LegState, ...]


@dataclass(frozen=True, slots=True)
class CompletedLegFacts:
    """Native cumulative fills plus the producer's current completed feature.

    first_fill_ms is native 100 ms time; average_entry belongs to THIS entry
    group, not the broker's ticker-aggregated holding. A quote-only boundary
    may permit an amendment but must not increment the observed price peak.
    """
    group_id: str
    boundary_ms: int
    first_fill_ms: int
    latest_fill_ms: int
    average_entry: float
    held_quantity: int
    confirmed_stop: float
    observed: bool
    high: float | None
    bid: float
    quote_valid: bool
    quote_age_us: int
    ten_second_mean_movement: float | None
    source_token: str
    first_fill_price: float


class NativeExecutionPort(Protocol):
    async def free_cash_after_reservations(self, account_id: str) -> float: ...
    async def publish_batch(self, state: BatchState) -> None:
        """Await the Keeper-fenced source and session lock before broker effects."""
        ...
    async def submit_leg(self, batch: EntryBatch, ordinal: int,
                         intent: StrategyIntent) -> SubmissionReceipt: ...
    async def publish_state(self, state: BatchState) -> None: ...
    async def amend_stop(self, source: LegStopAmendment,
                         facts: CompletedLegFacts) -> float:
        """Return the confirmed stop only after native OMS acknowledgement."""
        ...


def _at(session: date, boundary: int) -> datetime:
    return (datetime.combine(session, time(4), tzinfo=ZoneInfo("America/New_York"))
            + timedelta(milliseconds=boundary)).astimezone(timezone.utc)


class StrategyFortyThreeCoordinator:
    """One deterministic coordinator per native Portfolio account/session."""

    def __init__(self, *, account_id: str, session_date: date,
                 source_token: str, port: NativeExecutionPort):
        if not account_id or type(session_date) is not date or not source_token:
            raise ValueError("Strategy 43 coordinator needs a pinned account/session/source")
        self.account_id = account_id
        self.session_date = session_date
        self.source_token = source_token
        self.port = port
        self.batches: dict[str, BatchState] = {}
        self.last_boundary_ms = 0
        self.poisoned = False
        self._busy = False

    async def decide(self, facts: Sequence[EntryFacts], *,
                     assignment_ids: dict[str, str]) -> BatchState | None:
        if self.poisoned or self._busy:
            raise RuntimeError("Strategy 43 coordinator is busy or needs cold recovery")
        self._busy = True
        try:
            return await self._decide(facts, assignment_ids=assignment_ids)
        except BaseException:
            self.poisoned = True
            raise
        finally:
            self._busy = False

    async def _decide(self, facts, *, assignment_ids):
        if not facts:
            return None
        if (len({row.ticker for row in facts}) != len(facts)
                or len({row.boundary_ms for row in facts}) != 1):
            raise ValueError("Strategy 43 decision needs one row per ticker at one boundary")
        boundary = facts[0].boundary_ms
        if boundary <= self.last_boundary_ms:
            raise ValueError("Strategy 43 decisions must advance in causal order")
        eligible = []
        for row in facts:
            if type(row) is not EntryFacts or row.source_token != self.source_token:
                raise ValueError("Strategy 43 facts changed their pinned producer authority")
            row.validate()
            geometry = entry_geometry(row, already_submitted=row.ticker in self.batches)
            if geometry is not None:
                eligible.append((incoming_score(row, geometry), row.ticker, row))
        self.last_boundary_ms = boundary
        if not eligible:
            return None
        _, ticker, winner = min(eligible, key=lambda item: (-item[0], item[1]))
        assignment = assignment_ids.get(ticker)
        if not assignment:
            raise ValueError("Strategy 43 winner lacks its native assignment")
        free_cash = await self.port.free_cash_after_reservations(self.account_id)
        batch = propose_batch(winner, account_id=self.account_id,
            assignment_id=assignment, free_cash_after_reservations=free_cash,
            already_submitted=False)
        if batch is None:
            return None  # The chosen winner cannot be replaced with a cheaper ticker.
        intents = entry_intents(batch, session_date=self.session_date)
        state = BatchState(self.session_date, batch, tuple(
            LegState(leg.ordinal, intent.intent_id, "", "submission_pending",
                     confirmed_stop=leg.stop_price)
            for leg, intent in zip(batch.legs, intents)))
        # Poison on an uncertain publication outcome. Never unlock/re-submit
        # merely because a transport exception obscured a committed receipt.
        self.batches[ticker] = state
        await self.port.publish_batch(state)
        for ordinal, intent in enumerate(intents, 1):
            receipt = await self.port.submit_leg(batch, ordinal, intent)
            if (type(receipt) is not SubmissionReceipt or receipt.ordinal != ordinal
                    or receipt.intent_id != intent.intent_id
                    or receipt.outcome not in {"submitted", "rejected"}
                    or (receipt.outcome == "submitted") != bool(receipt.group_id)
                    or receipt.group_id and any(receipt.group_id == leg.group_id
                                                for leg in state.legs)):
                raise RuntimeError("Strategy 43 native submission receipt differs from its leg")
            legs = list(state.legs)
            legs[ordinal - 1] = replace(legs[ordinal - 1],
                group_id=receipt.group_id, outcome=receipt.outcome)
            state = replace(state, legs=tuple(legs))
            self.batches[ticker] = state
            await self.port.publish_state(state)
        return state

    async def manage(self, ticker: str, facts: Sequence[CompletedLegFacts]) -> BatchState:
        if self.poisoned or self._busy:
            raise RuntimeError("Strategy 43 coordinator is busy or needs cold recovery")
        self._busy = True
        try:
            return await self._manage(ticker, facts)
        except BaseException:
            self.poisoned = True
            raise
        finally:
            self._busy = False

    async def _manage(self, ticker, facts):
        state = self.batches[ticker]
        if len({row.group_id for row in facts}) != len(facts):
            raise ValueError("Strategy 43 management repeated a native group")
        updates = {row.group_id: row for row in facts}
        if set(updates) - {leg.group_id for leg in state.legs if leg.group_id}:
            raise ValueError("Strategy 43 management references a foreign native group")
        legs = list(state.legs)
        for index, leg in enumerate(legs):
            row = updates.get(leg.group_id)
            if row is None:
                continue
            if (type(row) is not CompletedLegFacts or row.source_token != self.source_token
                    or type(row.boundary_ms) is not int or row.boundary_ms % 1000
                    or not state.batch.facts.boundary_ms < row.boundary_ms <= state.batch.facts.session_end_ms
                    or row.boundary_ms <= leg.managed_boundary_ms
                    or type(row.held_quantity) is not int or row.held_quantity < 0
                    or row.held_quantity > state.batch.legs[index].quantity
                    or type(row.first_fill_ms) is not int or type(row.latest_fill_ms) is not int
                    or not 0 <= row.first_fill_ms <= row.latest_fill_ms <= row.boundary_ms
                    or row.first_fill_ms % 100 or row.latest_fill_ms % 100
                    or row.latest_fill_ms < leg.latest_fill_ms
                    or leg.first_fill_ms and row.first_fill_ms != leg.first_fill_ms
                    or row.first_fill_ms and (not isfinite(row.first_fill_price) or row.first_fill_price <= 0)
                    or row.held_quantity and (row.first_fill_ms <= state.batch.facts.boundary_ms
                        or not isfinite(row.average_entry) or row.average_entry <= 0)
                    or not isfinite(row.confirmed_stop) or row.confirmed_stop < leg.confirmed_stop
                    or type(row.observed) is not bool):
                raise ValueError("Strategy 43 management needs causal group-level native fills")
            peak = leg.peak_after_entry or row.first_fill_price
            if (row.observed and row.held_quantity and row.first_fill_ms
                    and row.boundary_ms > ((row.first_fill_ms + 999) // 1000) * 1000):
                if row.high is None or not isfinite(row.high) or row.high <= 0:
                    raise ValueError("Strategy 43 observed high is missing")
                peak = max(peak, row.high)
            stop = row.confirmed_stop
            if row.held_quantity:
                proposed = adaptive_stop(boundary_ms=row.boundary_ms,
                    first_fill_ms=row.first_fill_ms, current_stop=stop,
                    average_entry=row.average_entry, peak_after_entry=peak,
                    bid=row.bid, quote_valid=row.quote_valid, quote_age_us=row.quote_age_us,
                    completed_ten_second_mean_movement=row.ten_second_mean_movement)
                if proposed > stop:
                    intent = StrategyIntent(
                        intent_id=str(uuid5(NAMESPACE_URL,
                            f"strategy-43-stop:{self.session_date}:{self.account_id}:"
                            f"{leg.intent_id}:{row.boundary_ms}")),
                        ticker=ticker, event_time=_at(self.session_date, row.boundary_ms),
                        action="replace_protective_stop", quantity=float(row.held_quantity),
                        reference_price=row.bid, invalidation_price=proposed,
                        reason="strategy_forty_three_adaptive_stop", metadata={})
                    stop = await self.port.amend_stop(LegStopAmendment(
                        leg.group_id, leg.intent_id, state.batch.assignment_id,
                        self.account_id, intent), row)
                    if not isfinite(stop) or not proposed <= stop < row.bid:
                        raise RuntimeError("Strategy 43 OMS did not confirm its proposed stop")
            legs[index] = replace(leg, first_fill_ms=row.first_fill_ms,
                average_entry=row.average_entry, held_quantity=row.held_quantity,
                confirmed_stop=stop, peak_after_entry=peak, latest_fill_ms=row.latest_fill_ms,
                managed_boundary_ms=row.boundary_ms)
        state = replace(state, legs=tuple(legs))
        self.batches[ticker] = state
        await self.port.publish_state(state)
        return state
