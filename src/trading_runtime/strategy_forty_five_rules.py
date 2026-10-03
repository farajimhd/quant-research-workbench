"""Pure rules for the proposed Strategy 45; not a published app executor.

Selected from the independently reconciled September 3 premarket grid.
Market facts must come from certified producers. This module neither builds
market products nor owns cash, orders, fills, journal publication or recovery.
The app adapter must submit fifteen separate single-slice intents together,
use shared Portfolio/OMS, and persist their source facts before broker effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import json
from math import floor, isfinite, log1p
from typing import Sequence
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy,
    ProtectionProfile, ProtectionSlice, StopRule, StopRuleType,
)
from .signals import CapitalRequest, StrategyIntent


STRATEGY_NUMBER = 45
STRATEGY_ID = "squeeze-grid-strategy"
DECISION_INTERVAL_MS = 1000
BROKER_INTERVAL_MS = 100
POSITION_COUNT = 15
ENTRY_CUTOFF_LEAD_MS = 300_000
LIQUIDATION_LEAD_MS = 60_000
SOURCE_CANDIDATE_ID = "3da6e376d107a979c18e96332386b0401d0797a472ce6bcf6226f1c9ced766b2"
SOURCE_CODE_COMMIT = "273124420277b4a7f80b5900c78c94b1d4e0d17b"
SOURCE_SESSION = "2026-09-03"
_NY = ZoneInfo("America/New_York")


def selected_candidate() -> dict:
    """Return a fresh exact research selection, without importing research."""
    return dict(entry="signal", macd_mask=0, macd_all=False, hold_seconds=0,
                positions=15, allocation="decreasing", target="structural",
                trailing="adaptive", initial_stop="swing", replacement=False)


def verify_selection() -> None:
    if sha256(json.dumps(selected_candidate(), sort_keys=True).encode()).hexdigest() != SOURCE_CANDIDATE_ID:
        raise ValueError("Strategy 45 selection differs from the reconciled winner")


def _finite(*values: float) -> bool:
    return all(type(value) in (int, float) and isfinite(value) for value in values)


def native_price(value: float, *, upward: bool = False) -> float:
    """Directional native order precision; producer/ranking facts stay raw."""
    if not _finite(value) or value <= 0:
        raise ValueError("Strategy 45 native order price must be finite and positive")
    with localcontext() as context:
        context.prec = 50
        result = Decimal(str(value)).quantize(Decimal("0.0000000001"),
            rounding=ROUND_CEILING if upward else ROUND_FLOOR)
    if result <= 0 or result >= Decimal("1e28"):
        raise ValueError("Strategy 45 native order price exceeds its journal contract")
    return float(result)


@dataclass(frozen=True, slots=True)
class ResistanceFact:
    level_id: str
    lower: float


@dataclass(frozen=True, slots=True)
class EntryFacts:
    """Completed producer facts; source hashes are integrity, not certification.

    Admission is the first certified signal rounded to the completed second.
    The swing must have been confirmed BEFORE this decision (the Torch history
    advances after its decision). Structure includes this completed second.
    The coordinator must separately certify source build and listing identity.
    """
    ticker: str
    boundary_ms: int
    admission_ms: int
    session_end_ms: int
    observed: bool
    quote_valid: bool
    quote_age_us: int
    bid: float
    ask: float
    close: float
    low: float
    high: float
    dollar_volume: float
    trades: int
    volume: float
    vwap: float
    previous_five_second_close: float
    previous_ten_second_mean_notional: float
    swing_low: float
    swing_available_ms: int
    structural_boundary_ms: int
    resistances: tuple[ResistanceFact, ...]
    source_token: str
    tradable: bool
    liquidity: object = None

    def validate(self) -> None:
        if (not self.ticker or self.ticker != self.ticker.upper()
                or type(self.boundary_ms) is not int or self.boundary_ms <= 0
                or self.boundary_ms % 1000
                or type(self.admission_ms) is not int or self.admission_ms < 0
                or self.admission_ms % 1000
                or type(self.session_end_ms) is not int or self.session_end_ms % 1000
                or not self.boundary_ms <= self.session_end_ms <= 57_600_000
                or any(type(x) is not bool for x in (self.observed, self.quote_valid, self.tradable))
                or type(self.quote_age_us) is not int or self.quote_age_us < 0
                or type(self.trades) is not int or self.trades < 0
                or type(self.swing_available_ms) is not int
                or type(self.structural_boundary_ms) is not int
                or self.swing_available_ms < 0 or self.structural_boundary_ms < 0
                or self.swing_available_ms % 1000 or self.structural_boundary_ms % 1000
                or self.swing_available_ms >= self.boundary_ms
                or self.structural_boundary_ms > self.boundary_ms
                or not isinstance(self.source_token, str) or not self.source_token
                or type(self.resistances) is not tuple
                or any(type(r) is not ResistanceFact or not r.level_id
                       or not _finite(r.lower) or r.lower <= 0 for r in self.resistances)
                or len({r.level_id for r in self.resistances}) != len(self.resistances)):
            raise ValueError("Strategy 45 needs typed causal completed producer facts")


def entry_geometry(facts: EntryFacts, *, already_submitted: bool) -> tuple[float, tuple[ResistanceFact, ...]] | None:
    """Immediate-signal-only entry: never retry a missed signal later."""
    facts.validate()
    if type(already_submitted) is not bool:
        raise TypeError("Submission lock must be Boolean")
    if (already_submitted or not facts.tradable or facts.ticker == "LGHL"
            or facts.boundary_ms != facts.admission_ms
            or facts.boundary_ms >= facts.session_end_ms - ENTRY_CUTOFF_LEAD_MS
            or not facts.observed or not facts.quote_valid or facts.quote_age_us > 1_000_000
            or not _finite(facts.bid, facts.ask, facts.close, facts.low, facts.high,
                           facts.dollar_volume, facts.volume, facts.swing_low)
            or not 0 < facts.bid <= facts.ask
            or (facts.ask - facts.bid) / max(facts.ask, .01) > .01
            or facts.dollar_volume < 1000 or facts.trades < 5
            or facts.structural_boundary_ms != facts.boundary_ms):
        return None
    from .strategy_forty_five_liquidity import passes
    if facts.liquidity is None or facts.liquidity.ticker != facts.ticker or facts.liquidity.decision_ms != facts.boundary_ms or not passes(facts.liquidity):
        return None
    stop = facts.swing_low - .01
    if not 0 < stop < facts.bid:
        return None
    # Distinct identities may have equal lower prices: research sorts lower
    # prices without deduplicating geometry. Do not change that policy here.
    overhead = sorted((r for r in facts.resistances if r.lower > facts.ask),
                      key=lambda r: (r.lower, r.level_id))[:POSITION_COUNT]
    if len(overhead) != POSITION_COUNT or any(r.lower - .01 <= facts.ask * 1.01 for r in overhead):
        return None
    return stop, tuple(overhead)


def incoming_score(facts: EntryFacts, geometry: tuple[float, tuple[ResistanceFact, ...]]) -> float:
    """Exact bounded Torch incoming ranking; ties use certified ticker order."""
    stop, targets = geometry
    def ratio(a: float, b: float, *, zero: float = 0, positive_inf: float = 0) -> float:
        if not _finite(a, b):
            return zero
        if b == 0:
            return positive_inf if a > 0 else zero
        return a / b
    def clip(x: float, lo: float, hi: float) -> float:
        return min(hi, max(lo, x))
    momentum = (facts.close / facts.previous_five_second_close - 1) / .03 if (
        _finite(facts.previous_five_second_close) and facts.previous_five_second_close > 0) else 0.
    strength = (facts.close / facts.vwap - 1) / .03 if (_finite(facts.vwap) and facts.vwap > 0) else 0.
    attention = clip(ratio(facts.dollar_volume, facts.previous_ten_second_mean_notional,
                           positive_inf=3), 0, 3) / 3
    liquidity = clip(facts.volume * .1 * facts.bid / 10_000, 0, 1)
    up = max(0., targets[0].lower - .01 - facts.ask)
    down = max(.01, facts.ask - stop)
    return (.30 * clip(momentum, -1, 1) + .20 * clip(strength, -1, 1)
            + .20 * attention + .15 * liquidity + .15 * up / (up + down))


@dataclass(frozen=True, slots=True)
class EntryLeg:
    ordinal: int
    quantity: int
    limit_price: float
    stop_price: float
    target_price: float
    target_level_id: str


@dataclass(frozen=True, slots=True)
class EntryBatch:
    account_id: str
    assignment_id: str
    facts: EntryFacts
    legs: tuple[EntryLeg, ...]


def propose_batch(facts: EntryFacts, *, account_id: str, assignment_id: str,
                  free_cash_after_reservations: float, already_submitted: bool) -> EntryBatch | None:
    """Suggest fifteen fixed quantities; Portfolio remains final authority.

    Free cash is supplied by Portfolio after pending buy costs/fees AND the
    conservative protective fee reserve. Do not derive it from broker cash.
    The coordinator chooses the highest-scoring eligible ticker BEFORE sizing;
    an unsized winner is not silently replaced by the next ranked ticker.
    """
    if (not account_id or not assignment_id or not _finite(free_cash_after_reservations)
            or free_cash_after_reservations < 0):
        raise ValueError("Strategy 45 sizing requires a valid Portfolio view")
    geometry = entry_geometry(facts, already_submitted=already_submitted)
    if geometry is None:
        return None
    stop, targets = geometry
    limit = native_price(facts.ask * 1.01)
    stop = native_price(stop)
    weights = tuple(1 / log1p(i) for i in range(1, 16))
    total = sum(weights)
    budget = max(0., free_cash_after_reservations - 15 * 5.)
    quantities = tuple(floor(budget * w / total / (limit + 2 * .005)) for w in weights)
    if min(quantities) < 1:
        return None
    return EntryBatch(account_id, assignment_id, facts, tuple(
        EntryLeg(i, quantity, limit, stop, native_price(target.lower - .01, upward=True), target.level_id)
        for i, (quantity, target) in enumerate(zip(quantities, targets), 1)))


def entry_intents(batch: EntryBatch, *, session_date: date) -> tuple[StrategyIntent, ...]:
    """One native single-slice intent per leg; never one sliced parent order."""
    if type(batch) is not EntryBatch or type(session_date) is not date:
        raise TypeError("Strategy 45 needs a typed entry batch and session date")
    batch.facts.validate()
    geometry = entry_geometry(batch.facts, already_submitted=False)
    if (not batch.account_id or not batch.assignment_id or len(batch.legs) != 15
            or geometry is None
            or tuple(r.ordinal for r in batch.legs) != tuple(range(1, 16))
            or any(type(r.quantity) is not int or r.quantity < 1
                   or not _finite(r.limit_price, r.stop_price, r.target_price)
                   or not 0 < r.stop_price < batch.facts.bid <= batch.facts.ask < r.limit_price < r.target_price
                   or not r.target_level_id for r in batch.legs)):
        raise ValueError("Strategy 45 batch has invalid independent legs")
    if any(leg.limit_price != native_price(batch.facts.ask * 1.01)
           or leg.stop_price != native_price(geometry[0])
           or leg.target_price != native_price(source.lower - .01, upward=True)
           or leg.target_level_id != source.level_id
           for leg, source in zip(batch.legs, geometry[1])):
        raise ValueError("Strategy 45 leg differs from its producer source")
    at = datetime.combine(session_date, time(4), tzinfo=_NY) + timedelta(milliseconds=batch.facts.boundary_ms)
    return tuple(StrategyIntent(
        intent_id=str(uuid5(NAMESPACE_URL, f"strategy-45:{session_date}:{batch.account_id}:"
                           f"{batch.assignment_id}:{batch.facts.ticker}:{batch.facts.boundary_ms}:{leg.ordinal}")),
        ticker=batch.facts.ticker, event_time=at.astimezone(timezone.utc),
        action="enter_long", quantity=float(leg.quantity), reference_price=batch.facts.ask,
        capital_request=CapitalRequest(mode="fixed_quantity", value=float(leg.quantity),
                                       minimum_quantity=float(leg.quantity), maximum_quantity=float(leg.quantity)),
        invalidation_price=leg.stop_price, profit_target_price=leg.target_price,
        execution_policy=ExecutionPolicy(
            policy_id="strategy-45-entry", name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(maximum_buy_price=leg.limit_price,
                                       deadline_ms=batch.facts.session_end_ms - ENTRY_CUTOFF_LEAD_MS - batch.facts.boundary_ms,
                                       maximum_reprices=0, persist_until_cancelled=False),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source="qmd"),
        protection_profile=ProtectionProfile("early-squeeze-fixed-stop-full-target", 1, slices=(
            ProtectionSlice("all", 1., StopRule(StopRuleType.FIXED_PRICE, price=leg.stop_price),
                            profit_target_price=leg.target_price),)),
        urgency="urgent", time_in_force="DAY", outside_rth=True,
        reason="strategy_forty_five_entry", metadata={}) for leg in batch.legs)


def adaptive_stop(*, boundary_ms: int, first_fill_ms: int, current_stop: float,
                  average_entry: float, peak_after_entry: float, bid: float,
                  quote_valid: bool, quote_age_us: int,
                  completed_ten_second_mean_movement: float | None) -> float:
    """Consume a producer's ten completed-second movement mean; only ratchet.

    The producer must require all ten consecutive observed changes; an empty
    second invalidates the mean. Peak excludes the first-filled 1s bucket.
    OMS must scope an amendment to exactly this leg's group, not all ticker
    groups. The fill clock and source fact must be durably journaled by adapter.
    """
    if (type(boundary_ms) is not int or boundary_ms <= 0 or boundary_ms % 1000
            or type(first_fill_ms) is not int or not 0 < first_fill_ms <= boundary_ms
            or first_fill_ms % 100 or type(quote_valid) is not bool
            or type(quote_age_us) is not int or quote_age_us < 0
            or not _finite(current_stop, average_entry, peak_after_entry)
            or min(current_stop, average_entry, peak_after_entry) <= 0):
        raise ValueError("Strategy 45 trailing requires typed post-fill facts")
    if (boundary_ms - first_fill_ms < 10_000 or not quote_valid
            or quote_age_us > 1_000_000 or not _finite(bid) or bid <= 0
            or completed_ten_second_mean_movement is None
            or not _finite(completed_ten_second_mean_movement)
            or completed_ten_second_mean_movement < 0):
        return current_stop
    candidate = peak_after_entry - max(3 * completed_ten_second_mean_movement, average_entry * .01)
    proposed = min(candidate, bid - .01)
    return max(current_stop, native_price(proposed)) if proposed > current_stop else current_stop


def protective_fee_reserve(*, held_and_pending_shares: int,
                           exit_fees_paid_by_role: Sequence[float]) -> float:
    """Conservative research reserve, including unused rotation's minimum."""
    if (type(held_and_pending_shares) is not int or held_and_pending_shares < 0
            or len(exit_fees_paid_by_role) != 4
            or any(not _finite(x) or x < 0 for x in exit_fees_paid_by_role)):
        raise ValueError("Strategy 45 needs four typed exit commission histories")
    if held_and_pending_shares == 0:
        return 0.
    return held_and_pending_shares * .005 + sum(max(0., 1. - paid) for paid in exit_fees_paid_by_role)
