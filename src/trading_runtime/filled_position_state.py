"""Shared execution-derived state rebuilt from normalized fills and fees.

Rule programs never receive this ledger. The chronological coordinator may use
position-open and age to select precomputed rule branches; management policies
may use actual cost. No signal, intent, reservation or acknowledgement counts
as a fill. Existing ClickHouse execution/commission facts remain the authority;
this bounded in-memory projection owns no table and writes no journal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import Mapping
from functools import wraps


def _exact_math(function):
    @wraps(function)
    def call(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return function(*args, **kwargs)
    return call


def _number(value, *, positive=False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0):
        raise ValueError("Execution money/quantity needs a finite typed Decimal")
    if value.as_tuple().exponent < -10 or value.copy_abs() >= Decimal(10) ** 28:
        raise ValueError("Execution value exceeds normalized Decimal(38, 10)")
    return value


def _price_number(value) -> Decimal:
    """Keep the native binary64 price projection separate from decimal money."""
    from .trading_price_codec import encode_trading_price
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("Fill price requires a finite positive Decimal projection")
    if value >= Decimal(10) ** 28:
        raise ValueError("Fill price exceeds the ledger magnitude bound")
    native = float(value)
    encode_trading_price(native)
    if Decimal(str(native)) != value:
        raise ValueError("Fill price differs from its exact native binary64 projection")
    return value


def _clock(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Execution state needs timezone-aware clocks")


@dataclass(frozen=True)
class FilledPosition:
    account_id: str
    strategy_id: str
    assignment_id: str
    listing_id: str
    ticker: str
    quantity: Decimal = Decimal(0)
    average_price: Decimal = Decimal(0)
    opened_at: datetime | None = None
    last_management_fill_at: datetime | None = None
    adds: int = 0
    realized_gross_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    closed_positions: int = 0
    winning_positions: int = 0
    episode_net_pnl: Decimal = Decimal(0)

    @property
    def position_open(self):
        return self.quantity > 0

    @property
    @_exact_math
    def net_realized_pnl(self):
        return self.realized_gross_pnl - self.fees

    @_exact_math
    def age_seconds(self, at: datetime) -> Decimal:
        _clock(at)
        if self.opened_at is None:
            return Decimal(0)
        if at < self.opened_at:
            raise ValueError("Position age cannot see a future fill")
        delta = at - self.opened_at
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


@dataclass(frozen=True)
class FillFact:
    execution_id: str
    order_id: str
    intent_id: str
    account_id: str
    strategy_id: str
    assignment_id: str
    listing_id: str
    ticker: str
    action: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime

    @property
    def key(self):
        return self.account_id, self.strategy_id, self.assignment_id, self.listing_id


@dataclass(frozen=True)
class FeeFact:
    execution_id: str
    commission: Decimal
    currency: str


class FilledPositionState:
    """One deterministic, bounded projection of a verified execution prefix.

    The caller must consume committed normalized facts in causal order. Cold
    recovery replays the same verified prefix through this same method. The
    explicit cap fails closed; it never evicts execution IDs needed for exact
    deduplication or drops late fees.
    """
    def __init__(self, *, maximum_executions: int = 1_000_000):
        if type(maximum_executions) is not int or not 1 <= maximum_executions <= 1_000_000:
            raise ValueError("Invalid execution-state bound")
        self.maximum_executions = maximum_executions
        self._fills: dict[str, FillFact] = {}
        self._fees: dict[str, FeeFact] = {}
        self._positions: dict[tuple, FilledPosition] = {}
        self._last_at: datetime | None = None
        self._add_intents: dict[tuple, set[str]] = {}
        self._episode_executions: dict[tuple, set[str]] = {}
        self._closed_episode_by_execution: dict[str, int] = {}
        self._closed_net: dict[int, Decimal] = {}

    @property
    def positions(self) -> Mapping[tuple, FilledPosition]:
        return MappingProxyType(self._positions)

    @property
    def pending_fee_count(self) -> int:
        return len(self._fills) - len(self._fees)

    def finalized_positions(self) -> Mapping[tuple, FilledPosition]:
        if self.pending_fee_count:
            raise ValueError("Financial results have pending execution commissions")
        return self.positions

    @_exact_math
    def terminal_financial_metrics(self) -> Mapping:
        """Project completed episodes from the same actual-fill/fee ledger.

        This proves ledger closure only, not a committed run or full session.
        Those independent authorities remain mandatory at comparison admission.
        """
        positions = self.finalized_positions()
        if any(position.position_open for position in positions.values()):
            raise ValueError("Terminal financial metrics require all positions closed")
        closed = sum(position.closed_positions for position in positions.values())
        # Search counts one entry per opened episode, not broker partial fills.
        # Terminal closure makes the episode count identical to closed positions.
        # Management fills retain their originating intent across partial fills.
        adds = {(fact.key, fact.intent_id) for fact in self._fills.values()
                if fact.action == "add_long"}
        reductions = {(fact.key, fact.intent_id) for fact in self._fills.values()
                      if fact.action in {"reduce_long", "take_profit"}}
        return MappingProxyType(dict(
            net_pnl=sum((position.net_realized_pnl for position in positions.values()), Decimal(0)),
            closed_positions=closed, filled_batches=closed,
            add_count=len(adds), reduce_count=len(reductions),
            winning_positions=sum(position.winning_positions for position in positions.values()),
            worst_position_pnl=min(self._closed_net.values(), default=Decimal(0)),
            terminal_valid=True))

    @_exact_math
    def apply_fill(self, fact: FillFact) -> FilledPosition:
        if type(fact) is not FillFact:
            raise TypeError("Execution projection accepts only typed fill facts")
        _clock(fact.filled_at)
        _number(fact.quantity, positive=True)
        _price_number(fact.price)
        if any(not isinstance(value, str) or not value for value in (
                fact.execution_id, fact.order_id, fact.intent_id, *fact.key, fact.ticker)):
            raise ValueError("Fill identity and attribution are required")
        if fact.action not in {"enter_long", "add_long", "reduce_long", "take_profit", "exit"}:
            raise ValueError("Fill action is outside the long-program contract")
        existing = self._fills.get(fact.execution_id)
        if existing is not None:
            if existing != fact:
                raise ValueError("Execution ID has conflicting fill content")
            return self._positions[fact.key]
        if len(self._fills) >= self.maximum_executions:
            raise ValueError("Execution state exceeds bounded recovery prefix")
        if self._last_at is not None and fact.filled_at < self._last_at:
            raise ValueError("Fill projection is out of causal order")
        state = self._positions.get(fact.key, FilledPosition(*fact.key, fact.ticker))
        if state.ticker != fact.ticker:
            raise ValueError("Position listing/ticker attribution changed")
        buying = fact.action in {"enter_long", "add_long"}
        if fact.action == "add_long" and not state.position_open:
            raise ValueError("An add fill requires an actual open position")
        if not buying and (not state.position_open or fact.quantity > state.quantity):
            raise ValueError("Reduction exceeds actual attributed position")
        values = dict(state.__dict__)
        if buying:
            q = state.quantity + fact.quantity
            with localcontext() as context:
                context.prec = 80
                basis = (state.quantity * state.average_price + fact.quantity * fact.price) / q
            values.update(quantity=q, average_price=basis,
                opened_at=state.opened_at if state.position_open else fact.filled_at,
                last_management_fill_at=fact.filled_at)
            if not state.position_open:
                values.update(adds=0, episode_net_pnl=Decimal(0))
                self._add_intents[fact.key] = set()
                self._episode_executions[fact.key] = set()
            if fact.action == "add_long" and fact.intent_id not in self._add_intents[fact.key]:
                values["adds"] += 1
                self._add_intents[fact.key].add(fact.intent_id)
        else:
            with localcontext() as context:
                context.prec = 80
                gain = fact.quantity * (fact.price - state.average_price)
            values.update(quantity=state.quantity - fact.quantity,
                realized_gross_pnl=state.realized_gross_pnl + gain,
                episode_net_pnl=state.episode_net_pnl + gain,
                last_management_fill_at=fact.filled_at)
        self._episode_executions[fact.key].add(fact.execution_id)
        if values["quantity"] == 0:
            episode = len(self._closed_net)
            self._closed_net[episode] = values["episode_net_pnl"]
            for execution_id in self._episode_executions[fact.key]:
                self._closed_episode_by_execution[execution_id] = episode
            values.update(average_price=Decimal(0), opened_at=None,
                closed_positions=state.closed_positions + 1,
                winning_positions=state.winning_positions + int(values["episode_net_pnl"] > 0))
        projected = FilledPosition(**values)
        self._positions[fact.key] = projected
        self._fills[fact.execution_id] = fact
        self._last_at = fact.filled_at
        return projected

    @_exact_math
    def apply_fee(self, fact: FeeFact) -> FilledPosition:
        if type(fact) is not FeeFact or fact.currency != "USD":
            raise ValueError("Commission projection requires typed USD fees")
        _number(fact.commission)
        fill = self._fills.get(fact.execution_id)
        if fill is None:
            raise ValueError("Commission requires its verified execution first")
        prior = self._fees.get(fact.execution_id)
        if prior is not None:
            if prior != fact:
                raise ValueError("Execution commission changed")
            return self._positions[fill.key]
        state = self._positions[fill.key]
        values = dict(state.__dict__)
        values["fees"] += fact.commission
        episode = self._closed_episode_by_execution.get(fact.execution_id)
        if episode is not None:
            old = self._closed_net[episode]
            new = old - fact.commission
            self._closed_net[episode] = new
            values["winning_positions"] += int(new > 0) - int(old > 0)
        else:
            values["episode_net_pnl"] -= fact.commission
        self._fees[fact.execution_id] = fact
        result = FilledPosition(**values)
        self._positions[fill.key] = result
        return result
