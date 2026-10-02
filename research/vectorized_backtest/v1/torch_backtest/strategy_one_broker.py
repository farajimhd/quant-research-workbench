"""Device-side broker contracts used by the Strategy 1 parity experiment.

The broker is a simulator, not a searched policy. It consumes acknowledged
orders and completed liquidity intervals. The policy never fabricates fills.
Shapes: account scalars [B], one listing's book [B,15], price histogram [B,K].
Three purchases each own parent/target/stop/repair-target/repair-stop registers.
Only active exposure is retained; completed campaigns can reuse these slots.
"""

from dataclasses import dataclass

import torch


@dataclass
class Book:
    remaining: torch.Tensor
    filled: torch.Tensor
    paid: torch.Tensor
    price: torch.Tensor
    submitted_ms: torch.Tensor
    active: torch.Tensor
    triggered: torch.Tensor
    reference: torch.Tensor
    reserved_risk_per_share: torch.Tensor
    group_open: torch.Tensor
    group_acquired: torch.Tensor
    group_cancelled: torch.Tensor

    @classmethod
    def empty(cls, batch, device):
        real = torch.zeros((batch, 15), device=device, dtype=torch.float64)
        integer = torch.zeros((batch, 15), device=device, dtype=torch.int64)
        boolean = torch.zeros((batch, 15), device=device, dtype=torch.bool)
        groups = torch.zeros((batch, 3), device=device, dtype=torch.float64)
        return cls(
            real.clone(),
            real.clone(),
            real.clone(),
            real.clone(),
            integer.clone(),
            boolean.clone(),
            boolean.clone(),
            groups.clone(),
            groups.clone(),
            groups.clone(),
            groups.clone(),
            torch.zeros_like(groups, dtype=torch.bool),
        )


def match_listing(
    book,
    x,
    cash,
    quantity,
    average,
    allocated_risk,
    allocated_average,
    marketable_participation=0.25,
    passive_participation=0.25,
    stop_slippage_bps=0.0,
):
    """Match a frozen eligible book in broker order, entirely on the device.

    Fixed Python loops describe topology and are unrolled by torch.compile;
    they never dispatch on tensor values. Batch B is independent candidates.
    Stops trigger in one interval and can fill only on a subsequent interval.
    Displayed/eligible-price liquidity is shared by side within this interval.
    Per-order commissions use cumulative max($1, shares*$0.005), so a series
    of partial fills does not pay the minimum repeatedly.
    """
    # Local lane lists are functional SSA: no partial writes alias a later
    # order calculation. Commit each full book tensor once after reconciliation.
    working = {
        name: list(getattr(book, name).unbind(-1))
        for name in [
            "remaining",
            "filled",
            "paid",
            "price",
            "submitted_ms",
            "active",
            "triggered",
            "reference",
            "reserved_risk_per_share",
            "group_open",
            "group_acquired",
            "group_cancelled",
        ]
    }
    # Freeze eligibility before parent activation; new children cannot consume
    # liquidity from the interval that completed their parent.
    eligible = book.active.clone() & (
        book.submitted_ms <= x["interval_start_ms"][:, None]
    )
    eligible = eligible & x["present"][:, None]
    consumed_buy = torch.zeros_like(cash)
    consumed_sell = torch.zeros_like(cash)
    realized = torch.zeros_like(cash)
    fees = torch.zeros_like(cash)
    events = []
    exit_started = [torch.zeros_like(cash, dtype=torch.bool) for _ in range(3)]
    quote = x["quote_valid"]
    for index in range(15):
        group, role = divmod(index, 5)
        buy, stop = (role == 0, role in (2, 4))
        remaining = working["remaining"][index].clone()
        was_triggered = working["triggered"][index].clone()
        limit = working["price"][index]
        allowed = eligible[:, index] & working["active"][index] & (remaining > 0)
        touch = x["ask"] if buy else x["bid"]
        touch_size = x["ask_size"] if buy else x["bid_size"]
        # Canonical evidence is on the 1/10,000 price grid and Strategy 1
        # orders are on a cent grid. Compare those integers: compiled reciprocal
        # arithmetic can put two equal decimal prices on opposite float sides.
        limit_int = torch.round(limit * 10_000)
        touch_int = torch.round(touch * 10_000)
        if stop:
            triggered = (
                allowed & (x["low"] > 0) & (torch.round(x["low"] * 10_000) <= limit_int)
            )
            working["triggered"][index] = working["triggered"][index] | triggered
            allowed = allowed & was_triggered
            marketable = quote
            available = torch.where(quote, touch_size, 0)
        else:
            marketable = quote & (
                touch_int <= limit_int if buy else touch_int >= limit_int
            )
            qualifying = (
                x["price_int"] <= limit_int[:, None]
                if buy
                else x["price_int"] >= limit_int[:, None]
            )
            passive = torch.where(qualifying, x["price_volume"], 0).sum(-1)
            available = torch.where(marketable, touch_size, passive)
        price = torch.where(marketable, touch, limit)
        if stop:
            price = (
                torch.round(price * (1 - stop_slippage_bps / 10000) * 10000000000.0)
                / 10000000000.0
            )
        consumed = consumed_buy if buy else consumed_sell
        participation = torch.where(
            marketable, marketable_participation, passive_participation
        )
        candidate = torch.minimum(
            remaining, (available * participation - consumed).clamp_min(0)
        )
        candidate = torch.floor(candidate + 1e-09)
        if buy:
            safe_price = price.clamp_min(1e-12)
            budget = cash + working["paid"][index]
            affordable = torch.minimum(
                (budget - 1) / safe_price,
                (budget - working["filled"][index] * 0.005) / (safe_price + 0.005),
            )
            candidate = torch.minimum(
                candidate, torch.floor(affordable.clamp_min(0) + 1e-09)
            )
        else:
            candidate = torch.minimum(candidate, quantity)
        fill = torch.where(allowed & (price > 0) & (available > 0), candidate, 0)
        cumulative = working["filled"][index] + fill
        commission = torch.where(
            fill > 0,
            torch.maximum(torch.ones_like(fill), cumulative * 0.005)
            - working["paid"][index],
            0,
        )
        working["remaining"][index] = working["remaining"][index] - fill
        working["filled"][index] = cumulative
        working["paid"][index] = working["paid"][index] + commission
        done = (fill > 0) & (working["remaining"][index] <= 1e-12)
        working["active"][index] = working["active"][index] & ~done
        fees = fees + commission
        before_quantity = quantity.clone()
        if buy:
            quantity = quantity + fill
            average = torch.where(
                fill > 0,
                (average * before_quantity + fill * price) / quantity.clamp_min(1),
                average,
            )
            allocated_average = torch.where(
                fill > 0,
                (
                    allocated_average * before_quantity
                    + fill * working["reference"][group]
                )
                / quantity.clamp_min(1),
                allocated_average,
            )
            allocated_risk = (
                allocated_risk + fill * working["reserved_risk_per_share"][group]
            )
            cash = cash - (fill * price + commission)
            consumed_buy = consumed_buy + fill
            working["group_acquired"][group] = working["group_acquired"][group] + fill
            working["group_open"][group] = working["group_open"][group] + fill
            working["active"][group * 5 + 1] = working["active"][group * 5 + 1] | done
            working["active"][group * 5 + 2] = working["active"][group * 5 + 2] | done
        else:
            realized = realized + fill * (price - average)
            quantity = quantity - fill
            allocated_risk = allocated_risk * torch.where(
                before_quantity > 0, quantity / before_quantity.clamp_min(1), 0
            )
            average = torch.where(quantity > 0, average, 0)
            allocated_average = torch.where(quantity > 0, allocated_average, 0)
            cash = cash + (fill * price - commission)
            consumed_sell = consumed_sell + fill
            working["group_open"][group] = working["group_open"][group] - fill
            exit_started[group] = exit_started[group] | (fill > 0)
            sibling = index + 1 if role in (1, 3) else index - 1
            working["remaining"][sibling] = (
                working["remaining"][sibling] - fill
            ).clamp_min(0)
            working["active"][sibling] = working["active"][sibling] & (
                working["remaining"][sibling] > 0
            )
        events.append(torch.stack((fill if buy else -fill, price, commission), -1))
    for group in range(3):
        root, target, stop, repair_target, repair_stop = (
            group * 5 + i for i in range(5)
        )
        cancelled = exit_started[group] & (working["remaining"][root] > 0)
        working["group_cancelled"][group] = (
            working["group_cancelled"][group] | cancelled
        )
        working["active"][root] = working["active"][root] & ~cancelled
        working["remaining"][root] = torch.where(
            cancelled, 0, working["remaining"][root]
        )
        parent_complete = (
            (working["group_acquired"][group] > 0)
            & ~working["group_cancelled"][group]
            & (working["remaining"][root] == 0)
        )
        held = working["group_open"][group].clamp_min(0)
        # Native OCA-key ordering trims original protection first. Earlier
        # slices retain their repair pair; final parent slices use originals.
        # Keep separate trigger/commission histories after completion.
        repair_held = torch.where(
            parent_complete,
            torch.minimum(working["remaining"][repair_stop], held),
            held,
        )
        original_held = (held - repair_held).clamp_min(0)
        repair_needed = repair_held > 0
        for slot in (target, stop):
            working["remaining"][slot] = torch.where(
                parent_complete,
                torch.minimum(working["remaining"][slot], original_held),
                working["remaining"][slot],
            )
            working["active"][slot] = parent_complete & (working["remaining"][slot] > 0)
        newly_repaired = (
            repair_needed
            & (working["remaining"][repair_target] == 0)
            & (working["filled"][repair_target] == 0)
        )
        for slot in (repair_target, repair_stop):
            working["submitted_ms"][slot] = torch.where(
                newly_repaired, x["boundary_ms"], working["submitted_ms"][slot]
            )
            working["remaining"][slot] = torch.where(repair_needed, repair_held, 0)
            working["active"][slot] = repair_needed
    for name, lanes in working.items():
        getattr(book, name).copy_(torch.stack(lanes, -1))
    return (
        cash,
        quantity,
        average,
        allocated_risk,
        allocated_average,
        realized,
        fees,
        torch.stack(events, 1),
    )
