"""Source-equivalence compiler for frozen structural lot requests.

No installed declaration, journal append or financial admission is supplied
here. A future native owner must independently reload the source certificate
and persist this complete plan before calling the shared actor. Content hashes
and exact certificate classes alone are not approval.
"""
from bisect import bisect_left
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from math import isfinite
from uuid import UUID

from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit, _validate_children
from src.backend.backtest_market_data import market_day_boundary
from .early_squeeze_breakout import CONTRACT, target_price
from .early_squeeze_price import midpoint, is_resistance
from .execution_policies import AddProtectionPolicy, ProtectionSlice, StopRule, StopRuleType
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .strategy_one_contract import ordinal_target
from .strategy_one_stateful import StrategyOneEntryProposal
from .strategy_one_v7_intervals import SESSION_MS, V7LevelInterval, clock_hash, interval_hash
from .signals import StrategyIntent


class FixedStructuralLotTargetCountIneligible(ValueError):
    """Valid causal geometry has too few distinct declared structural targets."""
    def __init__(self, required: int, available: int):
        if (type(required) is not int or type(available) is not int
                or not 2 <= required <= 32 or not 1 <= available < required):
            raise ValueError('Invalid structural target shortage counts')
        self.required = required
        self.available = available
        super().__init__('Missing distinct higher structural targets')


@dataclass(frozen=True, slots=True)
class FixedStructuralLotTarget:
    level_id: str
    lower: float
    upper: float
    confirmed_at_ms: int
    historical: bool
    price: float
    role: str
    transition_from: str


@dataclass(frozen=True, slots=True)
class FixedStructuralLotEntry:
    proposal: StrategyOneEntryProposal
    session_date: date
    policy: FixedStructuralLotPolicy
    tick: float
    source_build_id: str
    interval_token: str
    source_attempt_id: str
    bars_attempt_id: str
    source_checkpoint_hash: str
    decoded_seed_hash: str
    seed_source_plan_hash: str
    seed_input_policy: str
    split_evidence_hash: str
    clock_count: int
    interval_count: int
    clock_hash: str
    interval_hash: str
    targets: tuple[FixedStructuralLotTarget, ...]


def prepare_fixed_structural_lot_entry(proposal, *, session_date, policy, intervals, tick):
    """Use causal lookup and preserve the original ordinal midpoint target."""
    _validate_entry_input(proposal, session_date=session_date, policy=policy, intervals=intervals, tick=tick)
    _validate_interval_ticker(intervals, session_date=session_date, ticker=proposal.ticker)
    return _select_fixed_structural_lot_entry(proposal, session_date=session_date,
        policy=policy, intervals=intervals, tick=tick)


def _validate_entry_input(proposal, *, session_date, policy, intervals, tick):
    if (type(proposal) is not StrategyOneEntryProposal or type(session_date) is not date
            or type(policy) is not FixedStructuralLotPolicy
            or type(intervals) is not CertifiedV7IntervalPlan
            or intervals.session_date != session_date.isoformat()
            or type(tick) is not float or not isfinite(tick) or tick <= 0
            or type(proposal.boundary_ms) is not int or not 0 < proposal.boundary_ms <= SESSION_MS
            or any(type(v) is not float or not isfinite(v) for v in
                   (proposal.reference_ask, proposal.initial_stop, proposal.initial_target))
            or not 0 < proposal.initial_stop < proposal.reference_ask < proposal.initial_target
            or type(proposal.ticker) is not str or not proposal.ticker
            or proposal.ticker != proposal.ticker.upper()
            or type(proposal.account_id) is not str or not proposal.account_id
            or type(proposal.assignment_id) is not str or not proposal.assignment_id
            or type(proposal.strategy_number) is not int or proposal.strategy_number <= 0
            or type(proposal.episode_start_ms) is not int or not 0 <= proposal.episode_start_ms <= proposal.boundary_ms
            or type(proposal.target_level_id) is not str or not proposal.target_level_id):
        raise ValueError('Fixed structural lots require exact entry and causal source')

def _validate_interval_ticker(intervals, *, session_date, ticker):
    intervals.__post_init__()
    index = intervals._tickers.index(ticker)
    unit = intervals.coverage[index]
    if type(unit) is not CertifiedV7IntervalUnit:
        raise ValueError('Exact fixed structural lot source unit required')
    seconds, rows = intervals.valid_seconds[index][1], intervals.intervals[index][1]
    # Share the producer's exact child causal/uniqueness validation, not a
    # second weaker interpretation of an ad-hoc constructed certificate.
    _validate_children(seconds, rows, origin_ms=int(market_day_boundary(session_date, 0).timestamp() * 1_000))
    if any(type(row) is not V7LevelInterval or type(row.lower) is not float or type(row.upper) is not float
           or type(row.confirmed_at_ms) is not int or type(row.historical) is not bool
           or type(row.valid_from_ms) is not int or type(row.valid_to_ms) is not int or type(row.ordinal) is not int
           or type(row.level_id) is not str or type(row.role) is not str
           or type(row.transition_from) is not str for row in rows):
        raise ValueError('Fixed structural lot geometry scalar types differ')
    if (type(unit.clock_count) is not int or type(unit.interval_count) is not int
            or unit.clock_count != len(seconds) or unit.interval_count != len(rows)
            or unit.clock_hash != clock_hash(seconds) or unit.interval_hash != interval_hash(rows)):
        raise ValueError('Fixed structural lot source content differs')
    for value in (intervals.token, unit.source_checkpoint_hash, unit.decoded_seed_hash,
                  unit.seed_source_plan_hash, unit.split_evidence_hash, unit.clock_hash, unit.interval_hash):
        if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Fixed structural lot source identity differs')
    if type(intervals.source_build_id) is not str or not 1 <= len(intervals.source_build_id) <= 256:
        raise ValueError('Fixed structural lot producer build differs')
    for value in (unit.attempt_id, unit.bars_attempt_id):
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError('Fixed structural lot producer attempt differs')
    return unit


def _select_fixed_structural_lot_entry(proposal, *, session_date, policy, intervals, tick):
    """Internal causal lookup after operation-local complete source validation."""
    policy.__post_init__()
    index = bisect_left(intervals._tickers, proposal.ticker)
    if index == len(intervals._tickers) or intervals._tickers[index] != proposal.ticker:
        raise ValueError("Entry ticker is outside prepared source")
    unit = intervals.coverage[index]
    levels = intervals.levels(proposal.ticker, boundary_ms=proposal.boundary_ms)
    original = ordinal_target(rows=levels, ask=proposal.reference_ask, tick=tick, broken_count=0)
    if (original is None or original['level']['unified_level_id'] != proposal.target_level_id
            or original['price'] != proposal.initial_target):
        raise ValueError('Original structural target differs from causal source')
    ordered = sorted((row for row in levels if is_resistance(row)),
                     key=lambda row: (midpoint(row), row['unified_level_id']))
    chosen = [next(row for row in ordered if row['unified_level_id'] == proposal.target_level_id)]
    previous = proposal.initial_target
    for row in ordered:
        price = target_price(row, tick, CONTRACT)
        if price > previous:
            chosen.append(row)
            previous = price
            if len(chosen) == policy.count:
                break
    if len(chosen) != policy.count:
        raise FixedStructuralLotTargetCountIneligible(policy.count, len(chosen))
    targets = tuple(FixedStructuralLotTarget(row['unified_level_id'], row['lower'], row['upper'],
                     row['confirmed_at_ms'], row['historical'], target_price(row, tick, CONTRACT),
                     row['role'], row.get('transition_from') or '')
                    for row in chosen)
    return FixedStructuralLotEntry(proposal, session_date, policy, tick, intervals.source_build_id,
        intervals.token, unit.attempt_id, unit.bars_attempt_id, unit.source_checkpoint_hash,
        unit.decoded_seed_hash, unit.seed_source_plan_hash, unit.seed_input_policy,
        unit.split_evidence_hash, unit.clock_count, unit.interval_count, unit.clock_hash,
        unit.interval_hash, targets)


def verify_fixed_structural_lot_entry(entry, *, intervals):
    if type(entry) is not FixedStructuralLotEntry:
        raise ValueError('Exact fixed structural lot source required')
    actual = prepare_fixed_structural_lot_entry(entry.proposal, session_date=entry.session_date,
        policy=entry.policy, intervals=intervals, tick=entry.tick)
    # Dataclass numeric equality must not conceal float/int or list/tuple aliases.
    if (type(entry.clock_count) is not int or type(entry.interval_count) is not int
            or type(entry.targets) is not tuple or any(type(t) is not FixedStructuralLotTarget
            or type(t.lower) is not float or type(t.upper) is not float or type(t.price) is not float
            or type(t.confirmed_at_ms) is not int or type(t.historical) is not bool
            or type(t.level_id) is not str or type(t.role) is not str or type(t.transition_from) is not str
            for t in entry.targets) or not _same_typed(entry, actual)):
        raise ValueError('Fixed structural lot source replay differs')


def fixed_structural_lot_intent(original, entry, *, intervals, intent_id):
    """Component-only semantic request; caller still owes own durable source admission."""
    verify_fixed_structural_lot_entry(entry, intervals=intervals)
    return _intent_from_verified_entry(original, entry, intent_id=intent_id)


def _intent_from_verified_entry(original, entry, *, intent_id, expected=None):
    from .strategy_one_intent import strategy_one_entry_intent
    if expected is None:
        expected = strategy_one_entry_intent(entry.proposal, session_date=entry.session_date)
    # This comparison includes capital, execution, protection, clock and the
    # factory identity derived from the proposal's account/assignment.
    if (type(original) is not StrategyIntent or not _same_typed(original, expected)
            or type(intent_id) is not str
            or str(UUID(intent_id)) != intent_id or intent_id == original.intent_id):
        raise ValueError('Original semantic request or own lot intent identity differs')
    profile = original.protection_profile
    if (profile is None or len(profile.slices) != 1
            or profile.slices[0].quantity_fraction != 1.
            or profile.slices[0].stop.price != entry.proposal.initial_stop
            or profile.slices[0].profit_target_price != entry.proposal.initial_target):
        raise ValueError('Original entry must have one inherited protection slice')
    slices = tuple(ProtectionSlice(f'lot-{index + 1}', numerator / denominator,
        StopRule(StopRuleType.FIXED_PRICE, price=entry.proposal.initial_stop),
        profit_target_price=target.price, inherit_profit_target=False)
        for index, (target, (numerator, denominator)) in enumerate(zip(entry.targets, entry.policy.weights)))
    selected = replace(profile, profile_id=entry.policy.profile_id, revision=entry.policy.version,
                       slices=slices, add_policy=AddProtectionPolicy.INDEPENDENT_FIXED_LOTS)
    return replace(original, intent_id=intent_id, protection_profile=selected)


def _same_typed(left, right):
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_same_typed(getattr(left, f.name), getattr(right, f.name)) for f in fields(left))
    if type(left) in (tuple, list):
        return len(left) == len(right) and all(_same_typed(a,b) for a,b in zip(left,right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_typed(left[k],right[k]) for k in left)
    return left == right
