from __future__ import annotations

import math
from dataclasses import dataclass, replace
from math import floor
from uuid import uuid4

from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.execution_policies import (
    AddProtectionPolicy,
    StopOrderType,
    TrailingRuleType,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.signals import StrategyIntent
from src.market_engine.events import MarketEvent


@dataclass(frozen=True, slots=True)
class StrategyOrderPlan:
    orders: tuple[OrderRequest, ...]
    cancel_oca_groups: tuple[str, ...] = ()
    cancel_strategy_protection: bool = False
    protection_reconciliation_required: bool = False
    batches: tuple[tuple[OrderRequest, ...], ...] = ()
    order_slice_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.order_slice_ids and len(self.order_slice_ids) != len(self.orders):
            raise ValueError("order_slice_ids must align with flattened orders")
        if self.batches:
            flattened = tuple(order for batch in self.batches for order in batch)
            if flattened != self.orders:
                raise ValueError("StrategyOrderPlan batches must flatten to orders")

    @property
    def broker_batches(self) -> tuple[tuple[OrderRequest, ...], ...]:
        return self.batches or ((self.orders,) if self.orders else ())


class IbkrStrategyOrderPlanner:
    """Translate semantic strategy intents into one IBKR-compatible order plan."""

    def plan(
        self,
        *,
        account_id: str,
        instrument: InstrumentContract,
        intent: StrategyIntent,
        strategy_id: str,
        strategy_revision: int,
        limit_offset_bps: float = 5.0,
    ) -> StrategyOrderPlan:
        quantity = _executable_quantity(instrument, float(intent.quantity))
        if quantity <= 0:
            raise ValueError("Order planning requires at least one executable share")
        prefix = f"{strategy_id[:14]}-v{strategy_revision}-{uuid4().hex[:12]}"
        position_side = str(intent.metadata.get("position_side") or "long").lower()
        if intent.action == "exit":
            side = "BUY" if position_side == "short" else "SELL"
            price = _aggressive_limit(intent.reference_price, side, limit_offset_bps)
            if intent.invalidation_price is None or intent.invalidation_price <= 0:
                raise ValueError("Full exit requires a broker-held fallback stop")
            orders = [
                _order(
                    account_id, instrument, f"{prefix}-exit", side, "LMT", quantity,
                    intent, price=price, grouped=True,
                ),
                _order(
                    account_id, instrument, f"{prefix}-fallback-stop", side, "STP", quantity,
                    intent, stop=intent.invalidation_price, grouped=True,
                ),
            ]
            if intent.trailing_amount is not None and intent.trailing_amount > 0:
                orders.append(
                    _order(
                        account_id, instrument, f"{prefix}-fallback-trail", side, "TRAIL", quantity,
                        intent, trailing=intent.trailing_amount, grouped=True,
                    )
                )
            return StrategyOrderPlan(
                orders=tuple(orders),
                cancel_strategy_protection=True,
            )
        if intent.action in {"reduce_long", "take_profit"}:
            price = _aggressive_limit(intent.reference_price, "SELL", limit_offset_bps)
            return StrategyOrderPlan(
                orders=(
                    _order(
                        account_id, instrument, f"{prefix}-reduce", "SELL", "LMT", quantity,
                        intent, price=price,
                    ),
                ),
                protection_reconciliation_required=True,
            )
        if intent.action in {"reduce_short", "cover"}:
            price = _aggressive_limit(intent.reference_price, "BUY", limit_offset_bps)
            return StrategyOrderPlan(
                orders=(
                    _order(
                        account_id, instrument, f"{prefix}-cover", "BUY", "LMT", quantity,
                        intent, price=price,
                    ),
                ),
                protection_reconciliation_required=True,
            )
        if intent.action not in {"enter_long", "add_long", "enter_short", "add_short"}:
            return StrategyOrderPlan(())

        short_entry = intent.action in {"enter_short", "add_short"}
        entry_side = "SELL" if short_entry else "BUY"
        exit_side = "BUY" if short_entry else "SELL"
        # Even "market" urgency is implemented as a bounded marketable limit by
        # the order manager, preserving price protection in volatile names.
        entry_type = "LMT"
        entry_price = _aggressive_limit(intent.reference_price, entry_side, limit_offset_bps)
        parent = _order(
            account_id, instrument, f"{prefix}-entry", entry_side, entry_type, quantity,
            intent, price=entry_price, grouped=True,
        )
        profile = intent.resolved_protection_profile()
        if profile is None:
            raise ValueError("Entry and add intents require a broker-held protection profile")
        volatility = float(intent.metadata.get("volatility") or 0)
        slice_quantities = _slice_quantities(quantity, tuple(item.quantity_fraction for item in profile.slices))
        batches: list[tuple[OrderRequest, ...]] = []
        flattened: list[OrderRequest] = []
        slice_ids: list[str] = []
        for index, (protection_slice, slice_quantity) in enumerate(zip(profile.slices, slice_quantities, strict=True)):
            slice_parent = _order(
                account_id,
                instrument,
                f"{prefix}-s{index + 1}-entry",
                entry_side,
                entry_type,
                slice_quantity,
                intent,
                price=entry_price,
                grouped=True,
            )
            if (
                intent.action in {"add_long", "add_short"}
                and profile.add_policy == AddProtectionPolicy.INHERIT_POSITION_STOP
            ):
                stop_price = float(intent.metadata.get("position_stop_price") or 0)
                if stop_price <= 0:
                    raise ValueError(
                        "inherit_position_stop add policy requires position_stop_price"
                    )
                if (not short_entry and stop_price >= intent.reference_price) or (
                    short_entry and stop_price <= intent.reference_price
                ):
                    raise ValueError("inherited position stop is on the wrong side of the add")
            else:
                stop_price = protection_slice.stop.resolve(
                    reference_price=intent.reference_price,
                    side="short" if short_entry else "long",
                    quantity=slice_quantity,
                    volatility=volatility,
                )
            stop_type = protection_slice.stop.order_type.value
            stop_limit_price = None
            if protection_slice.stop.order_type == StopOrderType.STOP_LIMIT:
                offset = float(protection_slice.stop.stop_limit_offset_bps or 0) / 10_000
                stop_limit_price = stop_price * (1 + offset if short_entry else 1 - offset)
            children: list[OrderRequest] = []
            target = protection_slice.profit_target_price or intent.profit_target_price
            valid_target = target is not None and (
                target < intent.reference_price if short_entry else target > intent.reference_price
            )
            if valid_target:
                children.append(
                    _order(
                        account_id,
                        instrument,
                        None,
                        exit_side,
                        "LMT",
                        slice_quantity,
                        intent,
                        parent_id=slice_parent.cOID,
                        price=target,
                        grouped=True,
                    )
                )
            children.append(
                _order(
                    account_id,
                    instrument,
                    None,
                    exit_side,
                    stop_type,
                    slice_quantity,
                    intent,
                    parent_id=slice_parent.cOID,
                    price=stop_limit_price,
                    stop=stop_price,
                    grouped=True,
                )
            )
            trailing = protection_slice.trailing
            if trailing.rule_type in {TrailingRuleType.BROKER_AMOUNT, TrailingRuleType.BROKER_PERCENT}:
                children.append(
                    _order(
                        account_id,
                        instrument,
                        None,
                        exit_side,
                        "TRAIL",
                        slice_quantity,
                        intent,
                        parent_id=slice_parent.cOID,
                        trailing=trailing.amount if trailing.rule_type == TrailingRuleType.BROKER_AMOUNT else trailing.percent,
                        trailing_type="amt" if trailing.rule_type == TrailingRuleType.BROKER_AMOUNT else "%",
                        grouped=True,
                    )
                )
            batch = (slice_parent, *children)
            batches.append(batch)
            flattened.extend(batch)
            slice_ids.extend([protection_slice.slice_id] * len(batch))
        return StrategyOrderPlan(
            orders=tuple(flattened),
            protection_reconciliation_required=intent.action in {"add_long", "add_short"},
            batches=tuple(batches),
            order_slice_ids=tuple(slice_ids),
        )


class RuntimeIbkrStrategyOrderPlanner:
    """Runtime adapter with explicit point-in-time instrument identity."""

    def __init__(
        self,
        instruments: dict[str, InstrumentContract],
        *,
        strategy_id: str,
        strategy_revision: int,
        run_id: str = "",
        limit_offset_bps: float = 5.0,
    ) -> None:
        self.instruments = {ticker.upper(): instrument for ticker, instrument in instruments.items()}
        self.strategy_id = strategy_id
        self.strategy_revision = strategy_revision
        self.run_id = run_id
        self.limit_offset_bps = limit_offset_bps
        self._planner = IbkrStrategyOrderPlanner()

    def upsert_instrument(self, instrument: InstrumentContract) -> None:
        """Register point-in-time identity for an assignment added during a run."""
        self.instruments[instrument.symbol.upper()] = instrument

    def plan(
        self,
        *,
        intent: StrategyIntent,
        account_id: str,
        event: MarketEvent | None,
    ) -> StrategyOrderPlan:
        instrument = self.instruments.get(intent.ticker.upper())
        if instrument is None:
            raise ValueError(f"No point-in-time instrument contract for strategy ticker: {intent.ticker}")
        planned = self._planner.plan(
            account_id=account_id,
            instrument=instrument,
            intent=intent,
            strategy_id=self.strategy_id,
            strategy_revision=self.strategy_revision,
            limit_offset_bps=self.limit_offset_bps,
        )
        enriched_by_identity = {
            id(order): replace(
                order,
                raw={
                    **dict(order.raw),
                    "canonical_run_id": self.run_id,
                    "canonical_strategy_id": self.strategy_id,
                    "canonical_strategy_revision": self.strategy_revision,
                    "canonical_metadata": {
                        **dict(intent.metadata),
                        "action": intent.action,
                        "reason": intent.reason,
                        "signal_price": intent.reference_price,
                    },
                },
            )
            for order in planned.orders
        }
        return replace(
            planned,
            orders=tuple(enriched_by_identity[id(order)] for order in planned.orders),
            batches=tuple(
                tuple(enriched_by_identity[id(order)] for order in batch)
                for batch in planned.batches
            ),
        )

    def should_cancel_strategy_protection(self, intent: StrategyIntent) -> bool:
        return intent.action == "exit"

    def protective_order_prefix(self) -> str:
        return f"{self.strategy_id[:14]}-v{self.strategy_revision}-"


def _order(
    account_id: str,
    instrument: InstrumentContract,
    client_id: str | None,
    side: str,
    order_type: str,
    quantity: float,
    intent: StrategyIntent,
    *,
    parent_id: str | None = None,
    price: float | None = None,
    stop: float | None = None,
    trailing: float | None = None,
    trailing_type: str | None = None,
    grouped: bool = False,
) -> OrderRequest:
    time_in_force, outside_rth = _smart_broker_session_fields(intent)
    return OrderRequest(
        acctId=account_id,
        conid=instrument.conid,
        secType=instrument.security_type,
        orderType=order_type,
        side=side,
        quantity=quantity,
        cOID=client_id[:64] if client_id else "",
        parentId=parent_id,
        ticker=instrument.symbol,
        tif=time_in_force,
        outsideRTH=outside_rth,
        price=price,
        auxPrice=stop,
        trailingAmt=trailing,
        trailingType=trailing_type or ("amt" if trailing is not None else None),
        listingExchange=instrument.exchange,
        isSingleGroup=grouped,
    )


def _smart_broker_session_fields(intent: StrategyIntent) -> tuple[str, bool]:
    if intent.metadata.get("session_routing") != "smart":
        return intent.time_in_force or "DAY", intent.outside_rth
    sessions = {
        str(value)
        for value in intent.metadata.get("eligible_sessions") or ["regular"]
    }
    # DAY plus outsideRTH is the portable IBKR contract for regular and
    # extended sessions. The planner remains responsible for selecting the
    # execution method; the broker adapter performs final capability checks.
    return "DAY", bool(sessions & {"premarket", "after_hours"})


def _slice_quantities(quantity: float, fractions: tuple[float, ...]) -> tuple[float, ...]:
    if quantity <= 0 or not fractions:
        raise ValueError("positive quantity and protection fractions are required")
    integral = math.isclose(quantity, round(quantity), abs_tol=1e-9)
    if integral:
        total = int(round(quantity))
        allocated = [floor(total * fraction) for fraction in fractions]
        for index in range(total - sum(allocated)):
            allocated[index % len(allocated)] += 1
        if any(value <= 0 for value in allocated):
            raise ValueError("approved quantity is too small for the requested protection slices")
        return tuple(float(value) for value in allocated)
    result = [quantity * fraction for fraction in fractions]
    result[-1] = quantity - sum(result[:-1])
    if any(value <= 0 for value in result):
        raise ValueError("protection slice quantity must be positive")
    return tuple(result)


def _executable_quantity(instrument: InstrumentContract, quantity: float) -> float:
    """Return the broker-executable quantity for this security contract.

    The application trades exchange-listed stocks by whole shares.  Portfolio
    sizing may calculate a fractional theoretical quantity, but that value is
    never an executable order authority.  Floor at the planner boundary so the
    parent, every protection child, later reconciliation, and the simulated
    broker all inherit one whole-share quantity.
    """

    if str(instrument.security_type or "").upper() == "STK":
        return float(floor(quantity + 1e-9))
    return quantity


def _aggressive_limit(reference_price: float, side: str, offset_bps: float) -> float:
    direction = 1 if side == "BUY" else -1
    return round(reference_price * (1 + direction * offset_bps / 10_000), 4)
