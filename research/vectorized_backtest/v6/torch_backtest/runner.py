"""Device-resident one-second replay with independently protected positions.

B independent counterfactual accounts share immutable market tape. Financial
state is [B,N,15], ticker setup state [B,N], and cash [B]. The clock remains
sequential; no Python loop over candidates, tickers, positions, or trades exists
in tick(). There are no tensor-to-host reads, database calls or frame operations
in tick(). Compiled/captured execution reuses fixed pointers across batches.
"""

import json
import math
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter

import torch

from .grid import MAX_POSITIONS, Settings
from .ledger_write import ledger_append,masked_ledger_append
from .remainder_update import remainder_update


def proportional_fill(wanted, capacity):
    """[B,N,M] integer demand → fills sharing [B,N] capacity exactly.

    Cumulative allocation assigns the integer remainder in stable slot order,
    unlike independently flooring fractions (which can waste all low capacity).
    Accounts retain independent capacity; slots within one ticker share it.
    """
    cumulative = wanted.cumsum(-1)
    total = cumulative[..., -1:]
    budget = torch.minimum(capacity[..., None], total).clamp_min(0)
    allocated = torch.floor(cumulative.to(torch.float64) * budget / total.clamp_min(1))
    # Previous cumulative demand is exactly cumulative-wanted. Avoid a shifted
    # cat/slice: PyTorch 2.12 Inductor fails code generation for masked modular indexing
    # on large listing axes. Preserve the same FP64 multiply/divide/floor order.
    prior = torch.floor(
        (cumulative - wanted).to(torch.float64) * budget / total.clamp_min(1)
    )
    return (allocated - prior).to(torch.int64)


class SqueezeRunner:
    def __init__(
        self,
        tape,
        candidates,
        settings=Settings(),
        *,
        backend="eager",
        maximum_state_gib=2.0,
        maximum_fills=16384,
        graph_steps=16,
        ledger_mode="inplace",
        slot_capacity=MAX_POSITIONS,
        masked_ledger=False,
    ):
        self.tape = tape.validate()
        self.settings = settings.validate()
        if backend not in ("eager", "compile", "cudagraph", "compiled_graph"):
            raise ValueError("Unknown backend; no automatic fallback")
        if backend in ("cudagraph", "compiled_graph") and tape.device.type != "cuda":
            raise ValueError("Captured execution requires CUDA")
        if type(maximum_fills) is not int or not 1 <= maximum_fills <= 1_000_000:
            raise ValueError("Invalid bounded ledger capacity")
        if type(graph_steps) is not int or not 1 <= graph_steps <= 64:
            raise ValueError("Capture steps must be 1..64")
        self.b, self.n = len(candidates), len(tape.tickers)
        if not 1 <= self.b <= 1024:
            raise ValueError("Batch must be 1..1024")
        self.backend, self.graph_steps = backend, graph_steps
        self.maximum_fills = maximum_fills
        if ledger_mode not in ("unique", "atomic", "inplace"):
            raise ValueError("Unknown ledger write mode")
        self.ledger_mode = ledger_mode
        self.masked_ledger=masked_ledger
        if type(slot_capacity) is not int or not 1<=slot_capacity<=MAX_POSITIONS:
            raise ValueError('Slot capacity must preserve the 1..15 lot contract')
        if any(c.positions>slot_capacity for c in candidates):raise ValueError('Strategy exceeds allocated lot capacity')
        self.slots=slot_capacity
        self.shape = (self.b, self.n, self.slots)
        # Include four distinct exit-order commission histories and bounded logs.
        estimate = self.b * self.n * self.slots * 480 + self.b * maximum_fills * 9 * 8
        if ledger_mode != "atomic":
            estimate += self.b * self.n * self.slots * 9 * 8
        if (
            not math.isfinite(maximum_state_gib)
            or maximum_state_gib <= 0
            or estimate > maximum_state_gib * 1024**3
        ):
            raise MemoryError(
                "Candidate/order/ledger buffers exceed the state envelope"
            )
        if tape.device.type == "cuda":
            free, _ = torch.cuda.mem_get_info(tape.device)
            if estimate > free * 0.65:
                raise MemoryError("Candidate state exceeds free GPU headroom")
        self.fixed_remainder_values = {
            "remainder_policy_id": torch.full((1, 1), settings.remainder_policy_id, dtype=torch.float64, device=tape.device),
            "maximum_total_order_age_seconds": torch.full((1, 1), settings.maximum_total_order_age_seconds, dtype=torch.float64, device=tape.device),
            "require_signal_valid": torch.full((1, 1), settings.require_signal_valid, dtype=torch.float64, device=tape.device),
            "maximum_retries": torch.full((1, 1), settings.maximum_retries, dtype=torch.float64, device=tape.device),
            "retry_interval_seconds": torch.full((1, 1), settings.retry_interval_seconds, dtype=torch.float64, device=tape.device),
            "maximum_entry_drift_fraction": torch.full((1, 1), settings.maximum_entry_drift_fraction, dtype=torch.float64, device=tape.device),
            "maximum_chase_bps": torch.full((1, 1), settings.maximum_chase_bps, dtype=torch.float64, device=tape.device),
            "entry_deadline_seconds": torch.full((1, 1), settings.entry_deadline_seconds, dtype=torch.float64, device=tape.device),
        }
        self._state_names = []

        def state(name, shape, dtype=torch.float64, value=0):
            setattr(
                self, name, torch.full(shape, value, dtype=dtype, device=tape.device)
            )
            self._state_names.append(name)

        for name in (
            "quantity",
            "requested_quantity",
            "remaining",
            "buy_filled",
            "buy_order_filled",
            "reduce_remaining",
            "add_count",
            "management_at",
            "buy_submitted",
            "buy_deadline",
            "buy_created",
            "buy_last_retry",
            "buy_retries",
            "first_fill",
            "last_high",
            "exit_kind",
        ):
            state(name, self.shape, torch.int64)
        for name in (
            "average",
            "buy_limit",
            "buy_reference",
            "buy_paid",
            "stop",
            "initial_stop",
            "target",
            "peak_price",
            "entry_reference",
        ):
            state(name, self.shape)
        state("exit_filled", self.shape + (5,), torch.int64)
        state("exit_paid", self.shape + (5,))
        for name in ("used", "previous_above", "previous_valid"):
            state(name, (self.b, self.n), torch.bool)
        for name in ("hold_since", "retest_phase", "retest_at"):
            state(name, (self.b, self.n), torch.int64)
        for name in ("retest_level", "retest_high"):
            state(name, (self.b, self.n))
        for name in (
            "cash",
            "realized",
            "fees",
            "equity",
            "equity_peak",
            "drawdown",
            "exposure_seconds",
            "long_hold_dollar_seconds",
            "stop_risk_dollar_seconds",
            "capital_dollar_seconds",
            "peak_reserved_stop_risk",
            "sold_share_seconds",
        ):
            state(name, (self.b,))
        for name in (
            "entered",
            "rejected_geometry",
            "rejected_size",
            "rotations",
            "fill_count",
            "expired_entry_shares",
            "policy_cancelled_entry_shares",
            "exit_cancelled_entry_shares",
            "terminal_cancelled_entry_shares",
            "rotation_wait",
            "rotation_exit_slot",
            "rotation_confirm_ticker",
            "rotation_confirm_slot",
            "rotation_since",
            "cooldown_until",
        ):
            state(name, (self.b,), torch.int64)
        state("overflow", (self.b,), torch.bool)
        state("financial_error", (self.b,), torch.bool)
        if ledger_mode != "atomic":
            # Inactive slots get distinct scratch locations, avoiding thousands
            # of zero atomics contending on one real ledger row. Active prefix
            # ranks are unique, so direct overwrite preserves exact fill rows.
            state("ledger_storage", (self.b, maximum_fills + self.n * self.slots, 9))
            self.ledger = self.ledger_storage[:, :maximum_fills]
        else:
            state("ledger", (self.b, maximum_fills, 9))
        state("index", (), torch.int64)
        state(
            "price_ring", (settings.retest_lookback_seconds, self.n), value=float("nan")
        )
        state(
            "close_ring",
            (settings.momentum_lookback_seconds, self.n),
            value=float("nan"),
        )
        state(
            "low_ring",
            (settings.swing_left_seconds + settings.swing_right_seconds + 1, self.n),
            value=float("nan"),
        )
        state("movement_ring", (settings.adaptive_window, self.n), value=float("nan"))
        state(
            "attention_ring",
            (settings.attention_lookback_seconds, self.n),
            value=float("nan"),
        )
        state("previous_close", (self.n,), value=float("nan"))
        state("swing_low", (self.n,), value=float("nan"))
        self.rank = torch.arange(1, self.slots+1, device=tape.device, dtype=torch.float64)[
            None, None
        ]
        self.ticker_axis = torch.arange(self.n, device=tape.device)[None]
        self.slot_axis = torch.arange(self.n * self.slots, device=tape.device)[None]
        self.macd_bits = torch.tensor((1, 2, 4, 8), device=tape.device)[None, None]
        self.batch_axis = torch.arange(self.b, device=tape.device)
        self.start = int(tape.provenance.get("start_second", int(tape.clocks[0])))
        self.end = int(tape.clocks[-1])
        # Device boundary buffers permit reuse of one captured graph across
        # dates without specializing Python epoch constants into generated code.
        self.start_boundary = torch.tensor(
            self.start, dtype=torch.int64, device=tape.device
        )
        self.end_boundary = torch.tensor(
            self.end, dtype=torch.int64, device=tape.device
        )
        self.parameters = torch.zeros(
            (self.b, 10), device=tape.device, dtype=torch.int64
        )
        self.set_candidates(candidates)
        self.completed = 0
        self.step = self.tick
        self.graph = self.remainder_graph = None
        self.setup_seconds = 0.0
        self.reset()

    def set_candidates(self, candidates):
        if len(candidates) != self.b:
            raise ValueError("Candidate batch shape changes require a new runner")
        rows = []
        for c in candidates:
            c.validate()
            if c.positions>self.slots:raise ValueError('Changing lot capacity requires a new runner')
            rows.append(
                (
                    ("signal", "hold", "retest", "macd").index(c.entry),
                    c.macd_mask,
                    int(c.macd_all),
                    c.hold_seconds,
                    c.positions,
                    ("equal", "decreasing", "increasing").index(c.allocation),
                    int(c.target == "structural"),
                    int(c.trailing == "adaptive"),
                    int(c.initial_stop == "swing"),
                    int(c.replacement),
                )
            )
        self.parameters.copy_(
            torch.tensor(rows, dtype=torch.int64, device=self.tape.device)
        )
        self.candidates = tuple(candidates)

    @property
    def fingerprint(self):
        return sha256(
            json.dumps(
                {
                    "settings": asdict(self.settings),
                    "source": self.tape.provenance,
                    "candidates": [asdict(c) for c in self.candidates],
                    "ledger_mode": self.ledger_mode,
                    "maximum_fills": self.maximum_fills,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def reset(self):
        for name in self._state_names:
            tensor = getattr(self, name)
            if name in (
                "price_ring",
                "close_ring",
                "low_ring",
                "movement_ring",
                "attention_ring",
                "previous_close",
                "swing_low",
            ):
                tensor.fill_(float("nan"))
            else:
                tensor.zero_()
        for name in ("cash", "equity", "equity_peak"):
            getattr(self, name).fill_(self.settings.initial_cash)
        for name in (
            "rotation_wait",
            "rotation_exit_slot",
            "rotation_confirm_ticker",
            "rotation_confirm_slot",
        ):
            getattr(self, name).fill_(-1)
        self.completed = 0

    def _recent_high(self):
        return self.price_ring.amax(0)[None]

    def _momentum_close(self):
        return self.close_ring[-1][None]

    def _attention_mean(self):
        return self.attention_ring.mean(0)[None]

    def _average_move(self):
        return self.movement_ring.mean(0)[None]

    def _advance_movement(self, close, observed):
        """Advance source history before adaptive amendments at this clock.

        Compact account allocators override source-history ownership while
        preserving this ordering relative to fills and trailing decisions.
        """
        movement = torch.where(
            observed & torch.isfinite(self.previous_close),
            (close - self.previous_close).abs(),
            float("nan"),
        )
        self.movement_ring.copy_(
            torch.cat((movement[None], self.movement_ring[:-1]), 0)
        )

    def _advance_source_history(self, close, low, high, observed, notional, above, quote):
        """Advance completed-bar histories after decisions, never on admission.

        Market histories are shared across accounts and must survive changes
        to which ticker identities occupy their financial-state slots.
        """
        if not getattr(self,'specialize',False) or not self.execution_key[3][1]:
            self.attention_ring.copy_(torch.cat((notional[None], self.attention_ring[:-1]), 0))
        if not getattr(self,'specialize',False):
            self.price_ring.copy_(torch.cat((torch.where(observed, high, float("nan"))[None], self.price_ring[:-1]), 0))
        if not getattr(self,'specialize',False) or not self.execution_key[3][0]:
            self.close_ring.copy_(torch.cat((torch.where(observed, close, float("nan"))[None], self.close_ring[:-1]), 0))
        if not getattr(self,'specialize',False) or self.execution_key[1][3]!=0:
            self.low_ring.copy_(torch.cat((torch.where(observed, low, float("nan"))[None], self.low_ring[:-1]), 0))
            self._confirm_swing()
        self._observe_rule_history(close, observed)
        self.previous_close.copy_(torch.where(observed, close, float("nan")))
        self.previous_above.copy_(above[None].expand(self.b, -1))
        valid_vwap = observed & quote & torch.isfinite(self._row("vwap"))
        self.previous_valid.copy_(valid_vwap[None].expand(self.b, -1))

    def _swing_level(self):
        return self.swing_low[None]

    def _confirm_swing(self):
        pivot = self.low_ring[self.settings.swing_right_seconds]
        confirmed = torch.isfinite(self.low_ring).all(0) & (
            pivot == self.low_ring.amin(0)
        )
        self.swing_low.copy_(torch.where(confirmed, pivot, self.swing_low))

    def _value(self, name, rank):
        """Scalar baseline; SearchRunner supplies [B,1,...] policy values."""
        return getattr(self.settings, name)

    def _entry_filter(self, now, close):
        return True

    def _observe_rule_history(self, close, observed):
        pass

    def _row(self, name):
        """Read evidence ending at the current UTC decision boundary.

        This is NOT the OHLC of the candle opening at that boundary. Broker
        and strategy share completed evidence but execute in tick's fixed order.
        """
        # Scalar device index selects [1,N,...] → [N,...]; no host .item().
        return (
            getattr(self.tape, name).index_select(0, self.index.reshape(1)).squeeze(0)
        )

    def _exit_fee_reserve(self):
        """[B,N,M] conservative cash cover for filled and pending positions.

        Shares can exit only once, but protection may switch between the four
        independent fee histories. Future commissions are bounded by one
        per-share charge plus every role's unpaid minimum. Reserve without
        assuming future sale proceeds; a small sale can cost more than it pays.
        """
        shares = self.quantity + self.remaining
        unpaid_minima = (
            (self.settings.minimum_order_fee - self.exit_paid).clamp_min(0).sum(-1)
        )
        reserve = shares.to(torch.float64) * self.settings.fee_per_share + unpaid_minima
        return torch.where(shares > 0, reserve, 0)

    def _log(self, qty, price, fee, now, side, reason):
        """Compact device ledger [B,E,9]; fail closed on capacity exhaustion."""
        if self.ledger_mode == "inplace":
            (masked_ledger_append if self.masked_ledger else ledger_append)(
                self.ledger_storage,
                self.fill_count,
                self.overflow,
                qty,
                price,
                fee,
                now,
                reason,
                self.index,
                self.slot_axis,
                side,
                self.maximum_fills,
            )
            return
        shape = (self.b, self.n * self.slots)
        active = qty.reshape(shape) > 0
        positions = self.fill_count[:, None] + active.cumsum(-1) - 1
        in_bounds = positions < self.maximum_fills
        self.overflow.logical_or_((active & ~in_bounds).any(-1))
        rows = torch.stack(
            (
                now.expand(shape),
                self.slot_axis.expand(shape) // self.slots,
                self.slot_axis.expand(shape) % self.slots,
                torch.full_like(qty.reshape(shape), side),
                qty.reshape(shape),
                price.expand(self.shape).reshape(shape),
                fee.reshape(shape),
                reason.expand(self.shape).reshape(shape),
                self.index.expand(shape),
            ),
            -1,
        ).to(torch.float64)
        rows = torch.where((active & in_bounds)[..., None], rows, 0)
        if self.ledger_mode != "atomic":
            destinations = torch.where(
                active & in_bounds, positions, self.maximum_fills + self.slot_axis
            )
            self.ledger_storage.scatter_(
                1, destinations[..., None].expand(-1, -1, 9), rows
            )
        else:
            positions = positions.clamp(0, self.maximum_fills - 1)
            self.ledger.scatter_add_(1, positions[..., None].expand(-1, -1, 9), rows)
        self.fill_count.add_(active.sum(-1))

    def _broker(
        self, now, close, low, high, quote, bid, ask, volume, fill_price, observed
    ):
        s = self.settings
        previous_quantity = self.quantity.clone()
        # The interval just completed may fill only previously submitted orders.
        spread = (ask - bid).nan_to_num(0).clamp_min(0)
        safe_fill = fill_price.nan_to_num(0)
        sell_price = (safe_fill - spread / 2).clamp_min(0)[None, :, None]
        buy_price = (safe_fill + spread / 2)[None, :, None]
        executable = (
            quote & torch.isfinite(fill_price) & (fill_price > 0) & (volume > 0)
        )
        capacity = (
            torch.floor(volume * s.participation)
            .to(torch.int64)[None]
            .expand(self.b, -1)
        )
        eligible_sell = (
            (self.exit_kind > 0)
            & (self.quantity > 0)
            & executable[None, :, None]
            & (sell_price > 0)
        )
        eligible_sell &= (self.exit_kind != 1) | (sell_price >= self.target)
        discretionary = (self.exit_kind == 1) | (self.exit_kind == 3) | (self.exit_kind == 5)
        eligible_sell &= ~discretionary | (
            now - self.first_fill >= s.minimum_position_hold_seconds
        )
        desired_exit=torch.where(self.exit_kind==5,torch.minimum(self.quantity,self.reduce_remaining),self.quantity)
        wanted = torch.where(eligible_sell, desired_exit, 0)
        sold = proportional_fill(wanted, capacity)
        role = (self.exit_kind - 1).clamp_min(0)[..., None]
        old_filled = self.exit_filled.gather(-1, role).squeeze(-1)
        old_paid = self.exit_paid.gather(-1, role).squeeze(-1)
        fee = torch.where(
            sold > 0,
            torch.maximum(
                torch.full_like(old_paid, s.minimum_order_fee),
                (old_filled + sold).to(torch.float64) * s.fee_per_share,
            )
            - old_paid,
            0,
        )
        self.exit_filled.scatter_add_(-1, role, sold[..., None])
        self.exit_paid.scatter_add_(-1, role, fee[..., None])
        self.realized.add_(((sell_price - self.average) * sold - fee).sum((1, 2)))
        self.cash.add_((sell_price * sold - fee).sum((1, 2)))
        self.fees.add_(fee.sum((1, 2)))
        self._log(sold, sell_price, fee, now, -1, self.exit_kind)
        self.sold_share_seconds.add_(
            (sold * (now - self.first_fill).clamp_min(0)).sum((1, 2))
        )
        self.quantity.sub_(sold)
        self.reduce_remaining.copy_(torch.where(self.exit_kind==5,(self.reduce_remaining-sold).clamp_min(0),0))
        # Any protective/rotation/terminal exit cancels the unfilled parent.
        self.exit_cancelled_entry_shares.add_(
            torch.where(self.exit_kind > 0, self.remaining, 0).sum((1, 2))
        )
        self.remaining.copy_(torch.where(self.exit_kind > 0, 0, self.remaining))
        self.exit_kind.copy_(torch.where(self.quantity > 0, self.exit_kind, 0))
        self.exit_kind.copy_(torch.where((self.exit_kind==5)&(self.reduce_remaining==0),0,self.exit_kind))
        age_ok = (now > self.buy_submitted) & (now <= self.buy_deadline)
        allowed = age_ok & (self.remaining > 0) & executable[None, :, None]
        allowed &= (
            (buy_price > 0) & (buy_price <= self.buy_limit) & (self.exit_kind == 0)
        )
        wanted = torch.where(allowed, self.remaining, 0)
        # Pending parents reserve risk at their worst permitted entry price.
        # Repricing cannot evade this account-wide gate: trim reservations
        # before consuming this interval's shared liquidity. No future marks.
        held_risk = (self.quantity * (self.average - self.stop).clamp_min(0)).sum((1, 2))
        risk_per_share = (self.buy_limit - self.stop).clamp_min(0)
        pending_risk = (self.remaining * risk_per_share).sum((1, 2))
        room = (self.equity.clamp_min(0) * s.maximum_stop_risk_fraction - held_risk).clamp_min(0)
        scale = (room / pending_risk.clamp_min(1e-12)).clamp(max=1)
        capped = torch.floor(self.remaining * scale[:, None, None]).to(torch.int64)
        self.policy_cancelled_entry_shares.add_((self.remaining - capped).sum((1, 2)))
        self.remaining.copy_(capped)
        wanted = torch.minimum(wanted, self.remaining)
        # All orders in one account share the SAME interval volume budget.
        bought = proportional_fill(wanted, (capacity - sold.sum(-1)).clamp_min(0))
        cumulative = self.buy_order_filled + bought
        buy_fee = torch.where(
            bought > 0,
            torch.maximum(
                torch.full_like(self.buy_paid, s.minimum_order_fee),
                cumulative.to(torch.float64) * s.fee_per_share,
            )
            - self.buy_paid,
            0,
        )
        spend = (buy_price * bought + buy_fee).sum((1, 2))
        self.financial_error.logical_or_(spend > self.cash + 1e-7)
        self.cash.sub_(spend)
        self.fees.add_(buy_fee.sum((1, 2)))
        self.realized.sub_(buy_fee.sum((1, 2)))
        old_qty = self.quantity.clone()
        self.quantity.add_(bought)
        self.average.copy_(
            torch.where(
                bought > 0,
                (self.average * old_qty + buy_price * bought)
                / self.quantity.clamp_min(1),
                self.average,
            )
        )
        first = (old_qty == 0) & (bought > 0)
        self.first_fill.copy_(torch.where(first, now, self.first_fill))
        self.last_high.copy_(torch.where(first, now, self.last_high))
        self.peak_price.copy_(torch.where(first, buy_price, self.peak_price))
        self.buy_filled.add_(bought)
        self.buy_order_filled.copy_(cumulative)
        self.buy_paid.add_(buy_fee)
        self.remaining.sub_(bought)
        # Expiry is finalized after completed evidence is evaluated below.
        # A retry cannot execute retroactively in this broker interval.
        if not hasattr(self, 'numeric') and self.settings.remainder_policy_id <= 1:
            # Preserve the standalone fixed-policy broker's expiry witness.
            # SearchRunner finalizes expiry exactly once in the policy stage;
            # broadcasting a class ID into this second broker mutation also
            # triggers Torch2.12's wide-axis fused codegen failure.
            expire_fixed = now >= self.buy_deadline
            self.expired_entry_shares.add_(torch.where(expire_fixed, self.remaining, 0).sum((1, 2)))
            self.remaining.copy_(torch.where(expire_fixed, 0, self.remaining))
        fill_stop = self.average * (1 - self._value("initial_stop_fraction", 3))
        initialize_percentage = first & (self.parameters[:, 8, None, None] == 0)
        self.stop.copy_(torch.where(initialize_percentage, fill_stop, self.stop))
        self.initial_stop.copy_(
            torch.where(initialize_percentage, fill_stop, self.initial_stop)
        )
        percentage = self.average * (
            1 + self.rank * self._value("target_step_fraction", 3)
        )
        self.target.copy_(
            torch.where(
                (self.parameters[:, 6, None, None] == 0) & (bought > 0),
                percentage,
                self.target,
            )
        )
        self._log(bought, buy_price, buy_fee, now, 1, torch.zeros_like(self.exit_kind))
        # Parent's fill interval cannot trigger its newly activated children.
        active = (previous_quantity > 0) & (self.quantity > 0) & observed[None, :, None]
        self.financial_error.logical_or_(
            (
                active & (~torch.isfinite(low) | ~torch.isfinite(high))[None, :, None]
            ).any((1, 2))
        )
        stop_hit = active & (low[None, :, None] <= self.stop)
        target_hit = active & (high[None, :, None] >= self.target)
        target_hit &= now - self.first_fill >= s.minimum_position_hold_seconds
        # Ambiguous completed bars use stop-first; an existing target can become
        # a stop, which has its own cumulative per-order fee history.
        kind = torch.where(target_hit & (self.exit_kind == 0), 1, self.exit_kind)
        self.exit_kind.copy_(torch.where(stop_hit, 2, kind))

    def _remainder_value(self, name):
        return self.fixed_remainder_values[name]

    def _manage_remainders(self, now, ask, signal_valid):
        """Amend after completed interval fills; never reuse observed liquidity.

        Original parent quantities/fees remain intact. Native mutation isolates
        the wide order transition from unsafe compiler fusion, not from CUDA
        capture. All added state participates in account reset/checkpoints.
        """
        remainder_update(
                self.remaining.flatten(1),
                self.buy_order_filled.flatten(1),
                self.buy_created.flatten(1),
                self.buy_retries.flatten(1),
                self.buy_last_retry.flatten(1),
                self.exit_kind.flatten(1),
                self.buy_reference.flatten(1),
                self.buy_limit.flatten(1),
                self.buy_paid.flatten(1),
                self.buy_submitted.flatten(1),
                self.buy_deadline.flatten(1),
                self.policy_cancelled_entry_shares,
                self.expired_entry_shares,
            [
                self._remainder_value("remainder_policy_id"),
                self._remainder_value("maximum_total_order_age_seconds"),
                self._remainder_value("require_signal_valid"),
                self._remainder_value("maximum_retries"),
                self._remainder_value("retry_interval_seconds"),
                self._remainder_value("maximum_entry_drift_fraction"),
                self._remainder_value("maximum_chase_bps"),
                self._remainder_value("entry_deadline_seconds"),
            ],
            now, ask, signal_valid, self.cash,
            self._exit_fee_reserve().sum((1, 2)),
            self.settings.fee_per_share, self.settings.minimum_order_fee,
        )

    def tick(self):
        """Finish [t-1s,t), then decide at t using ONLY completed evidence.

        _row is end-labelled: current close/high/low are candle t-1 in
        open-labelled notation. The candle [t,t+1s) is still inaccessible.
        Broker processing precedes submissions and stop amendments, so none
        of those new decisions can act retroactively on the interval just read.
        """
        s, p = self.settings, self.parameters
        now = self.tape.clocks.index_select(0, self.index.reshape(1)).squeeze(0)
        close, low, high = self._row("close"), self._row("low"), self._row("high")
        observed = self._row("observed") & torch.isfinite(close)
        bid, ask, quote = self._row("bid"), self._row("ask"), self._row("quote_valid")
        volume, notional, trades = (
            self._row("volume"),
            self._row("notional"),
            self._row("trades"),
        )
        self._broker(
            now,
            close,
            low,
            high,
            quote,
            bid,
            ask,
            volume,
            self._row("fill_price"),
            observed,
        )
        above = (
            observed
            & quote
            & torch.isfinite(self._row("vwap"))
            & (close > self._row("vwap"))
        )
        active_signal = (now >= self.tape.admission)[None] & (
            now - self.tape.admission <= self._value("maximum_signal_age_seconds", 2)
        )
        watching = active_signal & ~self.used & (now >= self.start_boundary)
        if not getattr(self,'specialize',False):
            crossed = above[None] & ~self.previous_above & self.previous_valid & watching
            self.hold_since.copy_(
                torch.where(
                    crossed, now, torch.where(above[None] & watching, self.hold_since, 0)
                )
            )
            hold = (
                above[None]
                & (self.hold_since > 0)
                & (now - self.hold_since >= p[:, 3, None])
            )
            line, signal = self._row("macd_line"), self._row("macd_signal")
            selected = (p[:, 1, None, None].bitwise_and(self.macd_bits)) != 0
            known = torch.isfinite(line)[None] & torch.isfinite(signal)[None]
            opened = (line > signal)[None] & known
            # ALL requires every selected lane known/open; ANY cannot use unknowns.
            macd = torch.where(
                p[:, 2, None] == 1,
                (~selected | opened).all(-1),
                (selected & opened).any(-1),
            )
            macd &= above[None]
            old_high = self._recent_high()
            breakout = (
                observed[None]
                & above[None]
                & (close[None] > old_high)
                & (self.previous_close[None] <= old_high)
            )
            phase = self.retest_phase.clone()
            invalid = ~above[None] | (
                now - self.retest_at > self._value("retest_timeout_seconds", 2)
            )
            invalid |= close[None] < self.retest_level * (
                1 - self._value("retest_tolerance_fraction", 2)
            )
            reset = invalid | ~watching
            phase = torch.where(reset, 0, phase)
            begin = (phase == 0) & breakout & watching
            self.retest_level.copy_(torch.where(begin, old_high, self.retest_level))
            self.retest_at.copy_(torch.where(begin, now, self.retest_at))
            touch = (phase == 1) & observed[None] & (now > self.retest_at)
            touch &= (
                low[None]
                <= self.retest_level * (1 + self._value("retest_tolerance_fraction", 2))
            ) & (close[None] >= self.retest_level)
            self.retest_high.copy_(torch.where(touch, high[None], self.retest_high))
            resume = (phase == 2) & observed[None] & (close[None] > self.retest_high)
            phase = torch.where(begin, 1, torch.where(touch, 2, phase))
            self.retest_phase.copy_(phase)
            gate = torch.where(
                p[:, 0, None] == 0,
                observed[None] & (now == self.tape.admission)[None],
                torch.where(
                    p[:, 0, None] == 1, hold, torch.where(p[:, 0, None] == 2, resume, macd)
                ),
            )
        else:
            gate=observed[None].expand(self.b,-1)
        spread = (ask - bid) / ask.clamp_min(s.price_tick)
        basic = (
            (observed & quote & (bid > 0) & (ask >= bid))[None]
            .expand(self.b, -1)
            .clone()
        )
        basic &= spread[None] <= self._value("maximum_spread_fraction", 2)
        basic &= (torch.isfinite(low) & torch.isfinite(high))[None]
        basic &= (notional[None] >= self._value("minimum_dollar_volume", 2)) & (
            trades[None] >= self._value("minimum_trade_count", 2)
        )
        terminal = now >= self.end_boundary - self._value(
            "terminal_exit_lead_seconds", 2
        )
        # V4 program entry replaces the fixed squeeze/MACD mode gate. Admission
        # and broker/cash/risk contracts remain independent of program syntax.
        if getattr(self, 'native_programs', False):
            gate = observed[None].expand(self.b, -1)
        ready = gate & basic & watching & ~terminal
        ready &= self._entry_filter(now, close)
        remainder_signal = above[None] if not getattr(self, 'native_programs', False) else observed[None]
        self._manage_remainders(now, ask, basic & active_signal & remainder_signal
                                & self._entry_filter(now, close) & ~terminal)
        # Shared market geometry is selected once per tick, not once per B.
        target_mode=self.execution_key[1][1] if getattr(self,'specialize',False) else -1
        if target_mode!=0:
            if self.tape.structural_targets is not None:
                # [N,15] already selected causally once per ticker/second, shared by B candidates.
                structural = self._row("structural_targets")[:, :self.slots] - s.price_tick
            else:
                geometry = (
                    (self.tape.level_from <= now)
                    & (now < self.tape.level_to)
                    & self.tape.level_resistance
                    & (self.tape.level_lower > ask[:, None])
                )
                levels = torch.where(
                    geometry, self.tape.level_lower - s.price_tick, float("inf")
                )
                structural = levels.topk(self.slots, dim=-1, largest=False, sorted=True).values
            structural = torch.where(
                self._row("structural_clock")[:, None], structural, float("inf")
            )
        if target_mode!=1:
            percentage=ask[None,:,None]*(1+self.rank*self._value('target_step_fraction',3))
        if target_mode==0:target=percentage
        elif target_mode==1:target=structural[None].expand(self.b,-1,-1)
        else:target=torch.where(p[:,6,None,None]==1,structural[None],percentage)
        stop_mode=self.execution_key[1][3] if getattr(self,'specialize',False) else -1
        if stop_mode==0:initial=ask[None]*(1-self._value('initial_stop_fraction',2))
        elif stop_mode==1:initial=self._swing_level()-s.price_tick
        else:initial=torch.where(p[:,8,None]==1,self._swing_level()-s.price_tick,ask[None]*(1-self._value('initial_stop_fraction',2)))
        slots = self.rank <= p[:, 4, None, None]
        geometry_ok = torch.isfinite(initial) & (initial > 0) & (initial < bid[None])
        geometry_ok &= (
            (~slots)
            | (
                torch.isfinite(target)
                & (
                    target
                    > ask[None, :, None]
                    * (1 + self._value("maximum_entry_drift_fraction", 3))
                )
            )
        ).all(-1)
        self.rejected_geometry.add_((ready & ~geometry_ok).sum(-1))
        ready &= geometry_ok
        if getattr(self,'specialize',False) and self.execution_key[3][0]:
            momentum=close.new_zeros((self.b,self.n))
        else:
            prior_close = self._momentum_close()
            # Comparable bounded score for incoming tickers and held positions.
            momentum = (
                ((close[None] / prior_close - 1) / self._value("momentum_scale", 2))
                .nan_to_num(0)
                .clamp(-1, 1)
            )
        strength = (
            ((close[None] / self._row("vwap") - 1) / self._value("strength_scale", 2))
            .nan_to_num(0)
            .clamp(-1, 1)
        )
        if getattr(self,'specialize',False) and self.execution_key[3][1]:
            attention=close.new_zeros((self.b,self.n))
        else:
            cap = self._value("attention_cap", 2)
            attention = (
                (notional[None] / self._attention_mean())
                .nan_to_num(0, posinf=1e100)
                .clamp_min(0)
            )
            attention = attention.clamp(max=cap) / cap
        liquidity = (
            volume[None] * s.participation * bid / self.settings.initial_cash
        ).clamp(0, 1)
        common_score = (
            self._value("momentum_weight", 2) * momentum
            + self._value("strength_weight", 2) * strength
            + self._value("attention_weight", 2) * attention
            + self._value("liquidity_weight", 2) * liquidity
        )
        up = (target[..., 0] - ask[None]).clamp_min(0)
        down = (ask[None] - initial).clamp_min(s.price_tick)
        incoming = common_score + self._value("reward_risk_weight", 2) * up / (
            up + down
        )
        pending_total = (self.remaining * self.buy_limit).sum((1, 2))
        remaining_fee = (
            torch.maximum(
                torch.full_like(self.buy_paid, s.minimum_order_fee),
                (self.buy_order_filled + self.remaining).to(torch.float64) * s.fee_per_share,
            )
            - self.buy_paid
        ).clamp_min(0)
        pending_total += torch.where(self.remaining > 0, remaining_fee, 0).sum((1, 2))
        # Held shares and unfilled parents retain their exit fee cover. Do not
        # spend that cash on another ticker before its protective orders finish.
        free_cash = (
            self.cash - pending_total - self._exit_fee_reserve().sum((1, 2))
        ).clamp_min(0)
        # An outstanding replacement waits for its exact position to finish.
        flat_qty = self.quantity.reshape(self.b, -1)
        outgoing_done = (
            flat_qty.gather(1, self.rotation_exit_slot.clamp_min(0)[:, None]).squeeze(1)
            == 0
        )
        waiting = (self.rotation_wait >= 0) & ~outgoing_done
        requested = (self.rotation_wait >= 0) & outgoing_done
        requested_mask = self.ticker_axis == self.rotation_wait[:, None]
        ranked_ready = ready & torch.where(requested[:, None], requested_mask, True)
        scores = torch.where(ranked_ready & ~waiting[:, None], incoming, -float("inf"))
        chosen = scores.argmax(-1)
        choose_mask = self.ticker_axis == chosen[:, None]
        weights = torch.where(
            p[:, 5, None, None] == 0,
            torch.ones_like(self.rank),
            torch.where(
                p[:, 5, None, None] == 1,
                1 / torch.log1p(self.rank),
                torch.log1p(self.rank),
            ),
        )
        weights = torch.where(slots, weights, 0)
        weights = weights / weights.sum(-1, keepdim=True)
        # Each new slot reserves its buy minimum and all distinct exit minima.
        # Buy and exit per-share charges each occur once across partial fills.
        exit_roles = self.exit_paid.shape[-1]
        budget = (
            free_cash
            - p[:, 4].to(torch.float64) * (1 + exit_roles) * s.minimum_order_fee
        ).clamp_min(0)
        limit = ask.nan_to_num(0)[None] * (
            1 + self._value("maximum_entry_drift_fraction", 2)
        )
        desired = torch.floor(
            budget[:, None, None] * weights / (limit[..., None] + 2 * s.fee_per_share)
        ).to(torch.int64)
        # Reserve risk for existing positions AND all unfilled parents before
        # submitting a new ticker. The one selected ticker uses the remaining
        # risk budget; additions/partial retries keep the same reservation.
        reserved_risk = (
            self.quantity * (self.average - self.stop).clamp_min(0)
            + self.remaining * (self.buy_limit - self.stop).clamp_min(0)
        ).sum((1, 2))
        risk_room = (self.equity.clamp_min(0) * s.maximum_stop_risk_fraction - reserved_risk).clamp_min(0)
        proposed_risk = (desired * (limit - initial).clamp_min(0)[..., None]).sum(-1)
        risk_scale = (risk_room[:, None] / proposed_risk.clamp_min(1e-12)).clamp(max=1)
        desired = torch.floor(desired * risk_scale[..., None]).to(torch.int64)
        size_ok = ((~slots) | (desired >= 1)).all(-1)
        chosen_ready = (ranked_ready & choose_mask).any(-1) & ~waiting
        can_enter = chosen_ready & (size_ok & choose_mask).any(-1)
        self.rejected_size.add_((chosen_ready & ~can_enter).to(torch.int64))
        enter = choose_mask & can_enter[:, None]
        order_mask = enter[..., None] & slots
        self.requested_quantity.copy_(
            torch.where(order_mask, desired, self.requested_quantity)
        )
        self.remaining.copy_(torch.where(order_mask, desired, self.remaining))
        self.buy_limit.copy_(torch.where(order_mask, limit[..., None], self.buy_limit))
        self.buy_submitted.copy_(torch.where(order_mask, now, self.buy_submitted))
        self.buy_created.copy_(torch.where(order_mask, now, self.buy_created))
        self.buy_last_retry.copy_(torch.where(order_mask, now, self.buy_last_retry))
        self.buy_retries.copy_(torch.where(order_mask, 0, self.buy_retries))
        self.buy_reference.copy_(torch.where(order_mask, ask[None, :, None], self.buy_reference))
        self.buy_deadline.copy_(
            torch.where(
                order_mask,
                now + self._value("entry_deadline_seconds", 3),
                self.buy_deadline,
            )
        )
        self.stop.copy_(torch.where(order_mask, initial[..., None], self.stop))
        self.initial_stop.copy_(
            torch.where(order_mask, initial[..., None], self.initial_stop)
        )
        self.target.copy_(torch.where(order_mask, target, self.target))
        self.entry_reference.copy_(
            torch.where(order_mask, ask[None, :, None], self.entry_reference)
        )
        self.used.logical_or_(enter)
        self.entered.add_(can_enter.to(torch.int64))
        # A missing/failed replacement setup releases the waiting request; its
        # cash remains cash, never a forced acquisition.
        self.rotation_wait.copy_(torch.where(requested, -1, self.rotation_wait))
        if not getattr(self,'specialize',False) or self.execution_key[1][4]!=0:
            holding_up = (self.target - bid[None, :, None]).clamp_min(0)
            holding_down = (bid[None, :, None] - self.stop).clamp_min(s.price_tick)
            stagnation = (
                (now - self.last_high).to(torch.float64)
                / self._value("stagnation_seconds", 3)
            ).clamp(0, 1)
            held_score = (
                common_score[..., None]
                + self._value("reward_risk_weight", 3)
                * holding_up
                / (holding_up + holding_down)
                - self._value("stagnation_weight", 3) * stagnation
            )
            replaceable = (
                (self.quantity > 0) & (self.exit_kind == 0) & (self.remaining == 0)
            )
            replaceable &= now - self.first_fill >= s.minimum_position_hold_seconds
            replaceable &= self.remaining.sum(-1, keepdim=True) == 0
            weak_scores = torch.where(replaceable, held_score, float("inf")).reshape(
                self.b, -1
            )
            weak = weak_scores.argmin(-1)
            weak_score = weak_scores.gather(1, weak[:, None]).squeeze(1)
            new_score = scores.gather(1, chosen[:, None]).squeeze(1)
            qualified = (p[:, 9] == 1) & chosen_ready & ~can_enter & ~waiting & ~requested
            if getattr(self, 'native_programs', False):
                qualified &= self._program_gate('replacement').gather(1, chosen[:, None]).squeeze(1)
            qualified &= (now >= self.cooldown_until) & torch.isfinite(weak_score)
            qualified &= weak // self.slots != chosen
            qualified &= new_score - weak_score >= self._value("replacement_margin", 1)
            same = (chosen == self.rotation_confirm_ticker) & (
                weak == self.rotation_confirm_slot
            )
            self.rotation_since.copy_(
                torch.where(qualified, torch.where(same, self.rotation_since, now), 0)
            )
            self.rotation_confirm_ticker.copy_(torch.where(qualified, chosen, -1))
            self.rotation_confirm_slot.copy_(torch.where(qualified, weak, -1))
            rotate = qualified & (
                now - self.rotation_since >= self._value("replacement_confirm_seconds", 1)
            )
            rotate_mask = (self.slot_axis == weak[:, None]) & rotate[:, None]
            self.exit_kind.copy_(
                torch.where(rotate_mask.reshape(self.shape), 3, self.exit_kind)
            )
            self.rotation_wait.copy_(torch.where(rotate, chosen, self.rotation_wait))
            self.rotation_exit_slot.copy_(
                torch.where(rotate, weak, self.rotation_exit_slot)
            )
            self.cooldown_until.copy_(
                torch.where(
                    rotate,
                    now + self._value("replacement_cooldown_seconds", 1),
                    self.cooldown_until,
                )
            )
            self.rotations.add_(rotate.to(torch.int64))
        # Amend protection only AFTER frozen interval processing.
        live = (self.quantity > 0) & observed[None, :, None] & (now > self.first_fill)
        new_high = live & (high[None, :, None] > self.peak_price)
        self.last_high.copy_(torch.where(new_high, now, self.last_high))
        self.peak_price.copy_(
            torch.where(new_high, high[None, :, None], self.peak_price)
        )
        trailing_mode=self.execution_key[1][2] if getattr(self,'specialize',False) else -1
        if trailing_mode!=0:
            self._advance_movement(close, observed)
            average_move = self._average_move()
            adaptive = self.peak_price - torch.maximum(
                average_move[..., None] * self._value("adaptive_multiplier", 3),
                self.average * self._value("minimum_trail_fraction", 3),
            )
            # SearchRunner pads history to 32 slots, but activation belongs to each
            # candidate's selected elapsed window, not that allocation capacity.
            enough = (
                now - self.first_fill >= self._value("adaptive_window", 3)
            ) & torch.isfinite(adaptive)
        if trailing_mode!=1:
            steps = torch.floor(
                (
                    (self.peak_price / self.average.clamp_min(s.price_tick) - 1)
                    / self._value("trail_up_fraction", 3)
                ).clamp_min(0)
                + 1e-10
            )
            stepped = (
                self.initial_stop
                + self.average * self._value("trail_stop_fraction", 3) * steps
            )
        if trailing_mode==0:proposed=stepped
        elif trailing_mode==1:proposed=torch.where(enough,adaptive,self.stop)
        else:proposed=torch.where(p[:,7,None,None]==1,torch.where(enough,adaptive,self.stop),stepped)
        proposed = torch.minimum(proposed, bid[None, :, None] - s.price_tick)
        amend = live & quote[None, :, None] & (self.exit_kind == 0)
        if getattr(self, 'native_programs', False):
            amend &= self._program_gate('trail')[..., None]
        self.stop.copy_(
            torch.where(amend, torch.maximum(self.stop, proposed), self.stop)
        )
        self.terminal_cancelled_entry_shares.add_(
            torch.where(terminal[..., None], self.remaining, 0).sum((1, 2))
        )
        self.remaining.copy_(torch.where(terminal[..., None], 0, self.remaining))
        self.exit_kind.copy_(
            torch.where(terminal[..., None] & (self.quantity > 0), 4, self.exit_kind)
        )
        # Expiry is a market exit intent for the NEXT broker interval. It does
        # not fabricate a fill at this decision or bypass liquidity limits.
        expired_hold = (self.quantity > 0) & (now - self.first_fill >= s.maximum_position_hold_seconds)
        self.exit_kind.copy_(torch.where(expired_hold & (self.exit_kind == 0), 3, self.exit_kind))
        if getattr(self, 'native_programs', False):
            discretionary = self._program_gate('exit')[..., None] & (self.quantity > 0)
            discretionary &= now - self.first_fill >= s.minimum_position_hold_seconds
            self.exit_kind.copy_(torch.where(discretionary & (self.exit_kind == 0), 3, self.exit_kind))
        # Source-history rings [H,N], not [B,N,H]. They advance once per second;
        # NaN gaps reset complete-window evidence instead of repeating a bar.
        self._advance_source_history(close, low, high, observed, notional, above, quote)
        mark = torch.where(torch.isfinite(close), close, 0)[None, :, None]
        self.financial_error.logical_or_(
            ((self.quantity > 0) & (mark <= 0)).any((1, 2))
        )
        self.equity.copy_(self.cash + (self.quantity * mark).sum((1, 2)))
        self.equity_peak.copy_(torch.maximum(self.equity_peak, self.equity))
        self.drawdown.copy_(
            torch.maximum(self.drawdown, self.equity_peak - self.equity)
        )
        self.exposure_seconds.add_((self.quantity > 0).sum((1, 2)))
        self.capital_dollar_seconds.add_((self.quantity * mark).sum((1, 2)))
        risk = self.quantity * (self.average - self.stop).clamp_min(0)
        self.stop_risk_dollar_seconds.add_(risk.sum((1, 2)))
        # Repriced pending parents must also fit at this decision boundary.
        pending_risk = (self.remaining * (self.buy_limit - self.stop).clamp_min(0)).sum((1, 2))
        room = (self.equity.clamp_min(0) * s.maximum_stop_risk_fraction - risk.sum((1, 2))).clamp_min(0)
        capped = torch.floor(self.remaining * (room / pending_risk.clamp_min(1e-12)).clamp(max=1)[:, None, None]).to(torch.int64)
        self.policy_cancelled_entry_shares.add_((self.remaining - capped).sum((1, 2)))
        self.remaining.copy_(capped)
        reserved_risk = (risk + self.remaining * (self.buy_limit - self.stop).clamp_min(0)).sum((1, 2))
        self.peak_reserved_stop_risk.copy_(torch.maximum(self.peak_reserved_stop_risk, reserved_risk))
        # Integrate start-of-next-interval exposure using only the completed
        # mark. One USD held one hour past the threshold contributes 3600.
        overdue = (self.quantity > 0) & (now - self.first_fill >= s.long_hold_seconds)
        self.long_hold_dollar_seconds.add_(
            torch.where(overdue, self.quantity * mark, 0).sum((1, 2))
        )
        self.financial_error.logical_or_(
            (self.cash < -1e-7) | ~torch.isfinite(self.equity)
        )
        self.index.add_(1)

    def compile(self):
        """Compile once; fixed parameter/state pointers permit in-place reuse."""
        started = perf_counter()
        self.step = (
            # Torch2.12's automatic layout padding can underallocate a later
            # native scatter view at wide/prime ticker counts (833 witness).
            # It also fails broadcast codegen for searchable remainder IDs.
            # Keep this graph's intermediates contiguous; arithmetic and
            # account tensor shapes are unchanged for every ledger mode.
            torch.compile(
                self.tick,
                fullgraph=True,
                options={"comprehensive_padding": False},
            )
            if self.backend in ("compile", "compiled_graph")
            else self.tick
        )
        if self.backend in ("compile", "compiled_graph"):
            self.reset()
            with torch.inference_mode():
                self.step()
            if self.tape.device.type == "cuda":
                torch.cuda.synchronize(self.tape.device)
        if self.backend in ("cudagraph", "compiled_graph"):
            stream = torch.cuda.Stream(device=self.tape.device)
            stream.wait_stream(torch.cuda.current_stream(self.tape.device))
            with torch.cuda.stream(stream), torch.inference_mode():
                for _ in range(2):
                    self.reset()
                    self.step()
            torch.cuda.current_stream(self.tape.device).wait_stream(stream)
            torch.cuda.synchronize(self.tape.device)
            count = min(self.graph_steps, len(self.tape.clocks))
            self.graph_steps = count
            self.reset()
            self.graph = torch.cuda.CUDAGraph()
            with torch.inference_mode(), torch.cuda.graph(self.graph):
                for _ in range(count):
                    self.step()
            remainder = len(self.tape.clocks) % count
            if remainder:
                self.reset()
                self.remainder_graph = torch.cuda.CUDAGraph()
                with torch.inference_mode(), torch.cuda.graph(self.remainder_graph):
                    for _ in range(remainder):
                        self.step()
        self.reset()
        self.setup_seconds = perf_counter() - started
        return self

    def run(self, *, reset=True, steps=None, progress=None):
        if reset:
            self.reset()
        remaining = len(self.tape.clocks) - self.completed
        if steps is not None and (
            type(steps) is not int or not 0 <= steps <= remaining
        ):
            raise ValueError("Invalid replay prefix")
        count = remaining if steps is None else steps
        if self.graph is not None and steps is not None and (not reset or count % self.graph_steps):
            raise ValueError(
                "Captured prefix requires reset and exact whole graph blocks"
            )
        started = perf_counter()
        updated = started
        with torch.inference_mode():
            if self.graph is not None:
                for _ in range(count // self.graph_steps):
                    self.graph.replay()
                    # Bound queued work for truthful UI cursors. One barrier per
                    # 256 ticks avoids per-tick reads/synchronization overhead.
                    if progress and (_ + 1) % max(1, 256 // self.graph_steps) == 0:
                        torch.cuda.current_stream(self.tape.device).synchronize()
                    if progress and perf_counter() - updated >= 1:
                        torch.cuda.current_stream(self.tape.device).synchronize()
                        progress(
                            {
                                "completed_seconds": self.completed
                                + (_ + 1) * self.graph_steps,
                                "total_seconds": len(self.tape.clocks),
                            }
                        )
                        updated = perf_counter()
                if count % self.graph_steps:
                    self.remainder_graph.replay()
                self.completed += count
            else:
                for _ in range(count):
                    self.step()
                    self.completed += 1
                    if progress and perf_counter() - updated >= 1:
                        progress(
                            {
                                "completed_seconds": self.completed,
                                "total_seconds": len(self.tape.clocks),
                            }
                        )
                        updated = perf_counter()
            if self.tape.device.type == "cuda":
                torch.cuda.current_stream(self.tape.device).synchronize()
        if bool(self.overflow.any()):
            raise RuntimeError("Fill ledger full; no truncation or completed result")
        if bool(self.financial_error.any()):
            raise RuntimeError("Invalid financial/mark state; candidate batch rejected")
        terminal = self.completed == len(self.tape.clocks)
        result = {
            name: getattr(self, name).clone()
            for name in (
                "cash",
                "equity",
                "realized",
                "fees",
                "drawdown",
                "entered",
                "rotations",
                "fill_count",
                "rejected_geometry",
                "rejected_size",
                "exposure_seconds",
                "long_hold_dollar_seconds",
                "stop_risk_dollar_seconds",
                "capital_dollar_seconds",
                "peak_reserved_stop_risk",
                "sold_share_seconds",
                "expired_entry_shares",
                "policy_cancelled_entry_shares",
                "exit_cancelled_entry_shares",
                "terminal_cancelled_entry_shares",
            )
        }
        result["requested_entry_shares"] = self.requested_quantity.sum((1, 2))
        result["filled_entry_shares"] = self.buy_filled.sum((1, 2))
        result["pending_entry_shares"] = self.remaining.sum((1, 2))
        result["filled_entry_orders"] = (self.buy_filled > 0).sum((1, 2))
        result["unfilled_entry_orders"] = (
            (self.requested_quantity > 0) & (self.buy_filled == 0)
        ).sum((1, 2))
        result["partially_filled_entry_orders"] = (
            (self.buy_filled > 0) & (self.buy_filled < self.requested_quantity)
        ).sum((1, 2))
        cancelled = (
            self.expired_entry_shares
            + self.policy_cancelled_entry_shares
            + self.exit_cancelled_entry_shares
            + self.terminal_cancelled_entry_shares
        )
        if not torch.equal(
            result["requested_entry_shares"],
            result["filled_entry_shares"] + result["pending_entry_shares"] + cancelled,
        ):
            raise RuntimeError(
                "Requested/filled/pending/cancelled parent quantity does not reconcile"
            )
        result["net_pnl"] = result["equity"] - self.settings.initial_cash
        result["objective"] = (
            result["net_pnl"] - self.settings.drawdown_weight * result["drawdown"]
        ) / self.settings.initial_cash
        result["open_quantity"] = self.quantity.sum((1, 2))
        result["open_positions"] = (self.quantity > 0).sum((1, 2))
        # entered counts submitted acquisition batches; activity constraints
        # must instead use child position orders that actually received fills.
        result["positions_opened"] = result["filled_entry_orders"]
        result['entry_retry_count'] = self.buy_retries.sum((1, 2))
        # A batch is one ticker acquisition. Fifteen child orders or many
        # partial-fill events still count as ONE activity unit.
        result["filled_batches"] = (self.buy_filled.sum(-1) > 0).sum(-1)
        result["sold_shares"] = self.buy_filled.sum((1, 2)) - self.quantity.sum((1, 2))
        result["terminal"] = terminal
        result["terminal_valid"] = (
            (result["open_quantity"] == 0)
            if terminal
            else torch.zeros(self.b, dtype=torch.bool, device=self.tape.device)
        )
        if terminal:
            # Residual exposure is explicit invalidity, not a fictitious final
            # fill or a profitable marked-position fitness.
            result["objective"] = torch.where(
                result["terminal_valid"], result["objective"], float("nan")
            )
        result["replay_seconds"] = perf_counter() - started
        if progress:
            progress(
                {
                    "completed_seconds": self.completed,
                    "total_seconds": len(self.tape.clocks),
                }
            )
        return result

    def state_dict(self):
        return {
            "fingerprint": self.fingerprint,
            "completed": self.completed,
            "state": {
                name: getattr(self, name).detach().cpu().clone()
                for name in self._state_names
            },
        }

    def load_state_dict(self, value):
        if value["fingerprint"] != self.fingerprint:
            raise ValueError("Checkpoint source/settings/candidates mismatch")
        if set(value["state"]) != set(self._state_names):
            raise ValueError("Checkpoint state contract mismatch")
        completed = value["completed"]
        if type(completed) is not int or not 0 <= completed <= len(self.tape.clocks):
            raise ValueError("Invalid checkpoint clock")
        for name, tensor in value["state"].items():
            target = getattr(self, name)
            if tensor.shape != target.shape or tensor.dtype != target.dtype:
                raise ValueError("Checkpoint tensor contract mismatch")
            target.copy_(tensor.to(self.tape.device))
        if int(self.index) != completed:
            raise ValueError("Checkpoint clock differs from state")
        self.completed = completed
