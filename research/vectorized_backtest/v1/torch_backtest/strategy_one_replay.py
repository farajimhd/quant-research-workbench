"""Causal Strategy 1 research replay over resident Torch tensors.

Market observations are atomic lanes. Entry/add expression graphs are compiled
once. Financial state is engine feedback: fills, acknowledged orders and prior
position witnesses. Tick sequencing is broker -> evidence -> management/entry.
Shared cash is folded in the app's lexical ticker order; independent candidate
accounts remain vectorized on dimension B. No GPU scalar controls Python flow.
"""

from dataclasses import fields
from time import perf_counter

import torch

from .strategy_one import (
    ProtectionRegisters,
    ResistanceRegisters,
    advance_protection,
    observe_resistance,
)
from .strategy_one_broker import Book, match_listing
from .strategy_one_program import (
    released_action_graphs,
    released_add_graph,
    released_entry_graph,
)
from .strategy_one_tape import FACT_FIELDS, MARKET_FIELDS
from .strategy_one_tensor_policy import AtomicReducer


def reprice_book(book, last_ask, global_event):
    """Fuse adaptive order updates; the three roots share one quote witness."""
    requested = torch.ceil(last_ask / 0.01 - 1e-9) * 0.01
    for group, root in enumerate((0, 5, 10)):
        valid = book.active[:, root] & global_event & (requested > 0)
        cancelled = valid & (
            torch.round(requested * 10000)
            <= torch.round(book.price[:, root + 2] * 10000)
        )
        book.active[:, root] &= ~cancelled
        book.remaining[:, root] = torch.where(cancelled, 0, book.remaining[:, root])
        book.group_cancelled[:, group] |= cancelled
        book.price[:, root] = torch.where(
            valid & ~cancelled, requested, book.price[:, root]
        )


def amend_book(book, management, target, stop):
    """One fused tensor update for all original and repair protective orders."""
    roles = torch.arange(15, device=book.price.device).remainder(5)
    desired = torch.where(
        ((roles == 1) | (roles == 3))[None], target[:, None], stop[:, None]
    )
    allowed = management[:, None] & (roles[None] != 0) & (book.remaining > 0)
    book.price.copy_(torch.where(allowed, desired, book.price))


class StrategyOneReplay:
    def __init__(
        self,
        tape,
        *,
        broker_ms=100,
        initial_cash=10_000,
        candidates=None,
        maximum_fills=8192,
        protection_values=None,
        entry_graph=None,
        add_graph=None,
        feature_bank=None,
        resistance_program=None,
        protection_program=None,
        action_graphs=None,
        action_values=None,
    ):
        if broker_ms not in (100, 1000):
            raise ValueError("Broker clock must be 100ms or 1s")
        self.tape, self.broker_ms = tape, broker_ms
        self.entry_graph = (entry_graph or released_entry_graph()).validate()
        self.add_graph = (add_graph or released_add_graph()).validate()
        self.action_graphs = (
            released_action_graphs() if action_graphs is None else action_graphs
        )
        if set(self.action_graphs) != {
            "capital_fraction",
            "initial_stop",
            "initial_target",
        }:
            raise ValueError("Policy needs all three atomic action-value contracts")
        for name, graph in self.action_graphs.items():
            graph.validate()
            if graph.gate or graph.output_unit != (
                "ratio" if name == "capital_fraction" else "price"
            ):
                raise ValueError(
                    "Action value graph has an incompatible output contract"
                )
        self.feature_bank = feature_bank
        builtins = {
            item.name
            for graph in (released_entry_graph(), released_add_graph())
            for item in graph.inputs
        }
        builtins |= {
            "available_cash",
            "working_stop",
            "working_target",
            "position_mark",
            "average_cost",
        }
        units = {
            item.name: item.unit
            for graph in (released_entry_graph(), released_add_graph())
            for item in graph.inputs
        }
        units.update(
            {
                "available_cash": "money",
                "working_stop": "price",
                "working_target": "price",
                "position_mark": "price",
                "average_cost": "price",
            }
        )
        kinds = {
            item.name: item.kind
            for graph in (released_entry_graph(), released_add_graph())
            for item in graph.inputs
        }
        kinds.update(
            {
                name: "real"
                for name in (
                    "available_cash",
                    "working_stop",
                    "working_target",
                    "position_mark",
                    "average_cost",
                )
            }
        )
        if feature_bank is not None:
            units.update({atom.name: atom.unit for atom in feature_bank.atoms})
        extra = (
            {atom.name for atom in feature_bank.atoms}
            if feature_bank is not None
            else set()
        )
        if builtins & extra:
            raise ValueError(
                "Market feature atoms cannot replace financial/causal engine inputs"
            )
        if feature_bank is not None and (
            feature_bank.values.shape
            != (tape.through_ms // 100, len(tape.tickers), len(extra))
            or feature_bank.values.device != tape.facts.device
        ):
            raise ValueError(
                "Feature bank differs from the resident session/listing/device contract"
            )
        for graph in (self.entry_graph, self.add_graph, *self.action_graphs.values()):
            if {item.name for item in graph.inputs} - builtins - extra:
                raise ValueError(
                    "Policy input lacks a declared engine/source dependency"
                )
            if any(item.unit != units[item.name] for item in graph.inputs):
                raise ValueError(
                    "Policy input unit differs from its atomic source contract"
                )
            if any(
                item.name in kinds and item.kind != kinds[item.name]
                for item in graph.inputs
            ):
                raise ValueError(
                    "Policy input kind differs from its atomic engine contract"
                )
        device = tape.facts.device
        self.theta = self.entry_graph.parameters(
            self.entry_graph.values if candidates is None else candidates, device
        )
        self.add_theta = self.add_graph.parameters(
            [self.add_graph.values] * len(self.theta), device
        )
        self.b, self.n = len(self.theta), len(tape.tickers)
        self.action_theta = {
            name: graph.parameters(
                (action_values or {}).get(name, [graph.values] * self.b), device
            )
            for name, graph in self.action_graphs.items()
        }
        if any(len(value) != self.b for value in self.action_theta.values()):
            raise ValueError(
                "Action parameter batch must match the entry candidate batch"
            )
        self.action_steps = {
            name: graph.evaluate for name, graph in self.action_graphs.items()
        }
        self.initial_cash, self.maximum_fills = initial_cash, maximum_fills
        shape = (self.b, self.n)
        self.state = {
            name: torch.zeros(shape, dtype=torch.float64, device=device)
            for name in (
                "quantity",
                "average",
                "mark",
                "allocated_risk",
                "allocated_average",
                "last_ask",
                "last_bid",
                "source_owned",
                "initialized",
                "source_ask",
                "source_stop",
                "source_target",
                "source_id",
                "position_high_int",
                "closed_boundary_ms",
                "prior_entry_resistance_id",
                "prior_position_high_int",
                "completed_entries",
                "purchase_groups",
                "low_int",
                "low_boundary_ms",
                "low_valid",
            )
        }
        self.cash = torch.full(
            (self.b,), initial_cash, dtype=torch.float64, device=device
        )
        self.realized, self.fees = (
            torch.zeros_like(self.cash),
            torch.zeros_like(self.cash),
        )
        # Boundary-local sufficient statistics. Refresh once after broker
        # reconciliation, then fold admitted reservations in lexical order.
        # These caches do not predict fills or survive as financial authority.
        self.funding = {
            name: torch.zeros_like(self.cash)
            for name in (
                "reserved",
                "reserved_risk",
                "gross",
                "equity",
                "capital",
                "allocated",
                "allocated_risk",
                "occupied_count",
            )
        }
        self.funding_occupied = torch.zeros(shape, dtype=torch.bool, device=device)
        self.index = torch.zeros(1, dtype=torch.int64, device=device)
        self.books = tuple(Book.empty(self.b, device) for _ in range(self.n))
        self.root_indices = torch.tensor((0, 5, 10), device=device, dtype=torch.int64)
        zeros = torch.zeros(shape, dtype=torch.float64, device=device)
        self.protection = ProtectionRegisters.empty(
            zeros.to(torch.int64), zeros, zeros, identities=tape.geometry.shape[2]
        )
        geometry = torch.zeros(
            (self.b, self.n, tape.geometry.shape[2]), dtype=torch.float64, device=device
        )
        self.resistance = ResistanceRegisters(
            zeros.to(torch.int64),
            zeros.to(torch.int64),
            geometry.bool(),
            geometry.clone(),
            geometry.clone(),
            geometry.bool(),
        )
        self.resistance_policy = AtomicReducer(
            "resistance", shape, device, program=resistance_program
        )
        self.protection_policy = AtomicReducer(
            "protection", shape, device, protection_values, program=protection_program
        )
        self.pending_clock, self.pending_lower, self.pending_mid = (
            geometry.clone(),
            geometry.clone(),
            geometry.clone(),
        )
        self.ledger = torch.zeros(
            (self.b, maximum_fills + 1, 6), dtype=torch.float64, device=device
        )
        self.fill_count = torch.zeros((self.b, 1), dtype=torch.int64, device=device)
        self.compiled_step = None
        self.broker_step = match_listing
        self.observer_step = observe_resistance
        self.protection_step = advance_protection
        self.entry_step = self.entry_graph.evaluate
        self.add_step = self.add_graph.evaluate
        self.graph = None
        self.graph_steps = 32
        self.compile_seconds = 0
        self.capture_seconds = 0
        self.reprice_step = reprice_book
        self.amend_step = amend_book
        self.reset()

    def update_candidates(self, *, entry=None, protection=None, actions=None):
        """Reuse compiled graphs for checked thresholds with unchanged topology.

        Search calls this between complete independent replays. Rule/input
        topology or batch-size changes require constructing a new runner and
        preparing its declared dependencies; they never recompile mid-session.
        """
        if entry is not None:
            values = self.entry_graph.parameters(entry, self.theta.device)
            if values.shape != self.theta.shape:
                raise ValueError(
                    "Candidate batch shape changes require new compilation"
                )
            self.theta.copy_(values)
        if protection is not None:
            self.protection_policy.update(protection)
        for name, rows in (actions or {}).items():
            if name not in self.action_graphs:
                raise ValueError("Unknown numeric policy action")
            values = self.action_graphs[name].parameters(rows, self.theta.device)
            if values.shape != self.action_theta[name].shape:
                raise ValueError(
                    "Action parameter batch shape cannot change during search"
                )
            self.action_theta[name].copy_(values)

    def reset(self):
        for value in self.state.values():
            value.zero_()
        self.cash.fill_(self.initial_cash)
        self.realized.zero_()
        self.fees.zero_()
        self.index.zero_()
        self.ledger.zero_()
        self.fill_count.zero_()
        for value in self.funding.values():
            value.zero_()
        self.funding_occupied.zero_()
        for book in self.books:
            for field in fields(book):
                getattr(book, field.name).zero_()
        for field in fields(self.protection):
            getattr(self.protection, field.name).zero_()
        for name in ("pending_min", "earned_min"):
            getattr(self.protection, name).fill_(float("inf"))
        for field in fields(self.resistance):
            getattr(self.resistance, field.name).zero_()
        self.pending_clock.zero_()
        self.pending_lower.zero_()
        self.pending_mid.zero_()

    def _slice(self, bank, index):
        return (
            bank.index_select(0, index).squeeze(0).unsqueeze(0).expand(self.b, -1, -1)
        )

    def _market(self, resolution):
        # The bank begins with the first completed source bucket. At 1.2s,
        # the 1s input is the bucket ending 1.0s, never the one ending 2.0s.
        completed = (self.index + 1).div(resolution // 100, rounding_mode="floor")
        row = self._slice(self.tape.market[resolution], (completed - 1).clamp_min(0))
        row = torch.where((completed > 0).reshape(1, 1, 1), row, 0)
        return {name: row[..., channel] for channel, name in enumerate(MARKET_FIELDS)}

    def _record(self, events, boundary):
        # [B,N,15,3] -> [B,N*15,6]. A prefix sum gives unique ledger slots to
        # fills. Non-fills write only the ignored sentinel slot zero.
        count = self.n * 15
        flat = events.reshape(self.b, count, 3)
        valid = flat[..., 0] != 0
        rank = valid.to(torch.int64).cumsum(-1)
        index = torch.where(valid, self.fill_count + rank, 0)
        torch._assert_async(
            torch.all(index <= self.maximum_fills),
            "Fill ledger exceeded its declared envelope",
        )
        listing = (
            torch.arange(self.n, device=events.device)
            .repeat_interleave(15)
            .expand(self.b, -1)
        )
        role = torch.arange(15, device=events.device).repeat(self.n).expand(self.b, -1)
        values = torch.cat(
            (
                boundary.expand(self.b, count, 1),
                listing[..., None],
                role[..., None],
                flat,
            ),
            -1,
        )
        values = torch.where(valid[..., None], values, 0)
        self.ledger.scatter_(1, index[..., None].expand(-1, -1, 6), values)
        self.fill_count += valid.sum(-1, keepdim=True)

    def _pending(self):
        return torch.stack(
            [
                book.active.index_select(-1, self.root_indices).any(-1)
                for book in self.books
            ],
            -1,
        )

    def _geometry(self, boundary):
        clock = self.tape.geometry_clock.index_select(0, self.index).squeeze(0)
        listing = torch.arange(self.n, device=clock.device)
        row = self.tape.geometry[clock // 1000, listing]
        row = row.unsqueeze(0).expand(self.b, -1, -1, -1)
        fresh = (boundary.reshape(()) - clock <= 1000).expand(self.b, -1)
        return torch.where(fresh[..., None, None], row, 0)

    def _copy_masked(self, target, proposed, mask):
        for field in fields(target):
            previous = getattr(target, field.name)
            value = getattr(proposed, field.name)
            expanded = mask if previous.ndim == mask.ndim else mask[..., None]
            previous.copy_(torch.where(expanded, value, previous))

    def _prepare_funding(self):
        """Compute whole-account summaries once, without proposal-dependent reads."""
        st = self.state
        reserved = torch.stack(
            [
                sum(
                    book.remaining[:, root] * book.reference[:, group]
                    for group, root in enumerate((0, 5, 10))
                )
                for book in self.books
            ],
            -1,
        )
        reserved_risk = sum(
            sum(
                book.remaining[:, root] * book.reserved_risk_per_share[:, group]
                for group, root in enumerate((0, 5, 10))
            )
            for book in self.books
        )
        occupied = (st["quantity"] > 0) | self._pending()
        held_value = st["quantity"] * st["mark"]
        gross = held_value.sum(-1)
        equity = self.cash + gross
        funded = (st["quantity"] * st["average"]).sum(-1)
        capital = self.cash + funded
        allocated = (st["quantity"] * st["allocated_average"]).sum(-1)
        values = {
            "reserved": reserved.sum(-1),
            "reserved_risk": reserved_risk,
            "gross": gross,
            "equity": equity,
            "capital": capital,
            "allocated": allocated,
            "allocated_risk": st["allocated_risk"].sum(-1),
            "occupied_count": occupied.sum(-1),
        }
        for name, value in values.items():
            self.funding[name].copy_(value)
        self.funding_occupied.copy_(occupied)

    def _fund(self, listing, desired, price, stop, fraction):
        """Native funding capacities using current boundary-local reservations.

        Reservation price is the admission reference, not a later adaptive
        limit. Allocated risk is released proportionally on exits. Each accepted
        order updates these summaries before the next listing/purchase request.
        """
        st, totals = self.state, self.funding
        held_value = st["quantity"][:, listing] * st["mark"][:, listing]
        risk = (price - stop).clamp_min(1e-12)
        capacities = torch.stack(
            (
                self.cash * fraction / price.clamp_min(1e-12),
                (self.cash * 0.995 - totals["reserved"]).clamp_min(0)
                / price.clamp_min(1e-12),
                (totals["equity"] - held_value).clamp_min(0) / price.clamp_min(1e-12),
                (totals["equity"] - totals["allocated"] - totals["reserved"]).clamp_min(
                    0
                )
                / price.clamp_min(1e-12),
                totals["capital"] * 0.08 / risk,
                (
                    totals["capital"] * 0.08
                    - totals["allocated_risk"]
                    - totals["reserved_risk"]
                ).clamp_min(0)
                / risk,
                (5_000_000 - totals["gross"] - totals["reserved"]).clamp_min(0)
                / price.clamp_min(1e-12),
                1_000_000 / price.clamp_min(1e-12),
                torch.full_like(price, 100_000),
            ),
            -1,
        ).amin(-1)
        quantity = torch.floor(capacities + 1e-9)
        admitted = (
            desired
            & (self.funding_occupied[:, listing] | (totals["occupied_count"] < 3))
            & (torch.round(price * 10000) > torch.round(stop * 10000))
            & (quantity > 0)
        )
        return torch.where(admitted, quantity, 0)

    def _submit(self, listing, group, quantity, price, stop, target, boundary):
        book = self.books[listing]
        admitted = quantity > 0
        root = group * 5
        for offset in range(5):
            slot = root + offset
            book.remaining[:, slot] = torch.where(
                admitted, quantity if offset < 3 else 0, book.remaining[:, slot]
            )
            book.filled[:, slot] = torch.where(admitted, 0, book.filled[:, slot])
            book.paid[:, slot] = torch.where(admitted, 0, book.paid[:, slot])
            value = price if offset == 0 else target if offset in (1, 3) else stop
            book.price[:, slot] = torch.where(admitted, value, book.price[:, slot])
            book.submitted_ms[:, slot] = torch.where(
                admitted, boundary.reshape(()), book.submitted_ms[:, slot]
            )
            book.active[:, slot] = torch.where(
                admitted, offset == 0, book.active[:, slot]
            )
            book.triggered[:, slot] &= ~admitted
        book.reference[:, group] = torch.where(
            admitted, price, book.reference[:, group]
        )
        book.reserved_risk_per_share[:, group] = torch.where(
            admitted, price - stop, book.reserved_risk_per_share[:, group]
        )
        book.group_acquired[:, group] = torch.where(
            admitted, 0, book.group_acquired[:, group]
        )
        book.group_open[:, group] = torch.where(admitted, 0, book.group_open[:, group])
        book.group_cancelled[:, group] &= ~admitted
        self.funding["reserved"].add_(torch.where(admitted, quantity * price, 0))
        self.funding["reserved_risk"].add_(
            torch.where(admitted, quantity * (price - stop), 0)
        )
        self.funding["occupied_count"].add_(
            (admitted & ~self.funding_occupied[:, listing]).to(torch.float64)
        )
        self.funding_occupied[:, listing] |= admitted
        return admitted

    def _manage(
        self,
        hundred,
        second,
        thirty,
        at,
        boundary,
        source_before,
        active_before,
        fresh,
        bid,
        ask,
    ):
        st = self.state
        # Producer geometry is selected only through its latest completed input
        # clock. The observer is ticker-owned and runs only on native active
        # streams; a flat watchlist does not invent resistance acceptances.
        geometry = self._geometry(boundary)
        lower, upper, eligible, resistance_role = geometry.unbind(-1)
        observe = (
            active_before
            & (second["present"] > 0)
            & (second["price_valid"] > 0)
            & (boundary.remainder(1000) == 0)
        )
        rx = {
            "boundary_ms": at.to(torch.int64),
            "open_int": second["open_int"].to(torch.int64),
            "close_int": second["close_int"].to(torch.int64),
            "present": lower > 0,
            "eligible_resistance": eligible > 0,
            "lower": lower,
            "upper": upper,
        }
        proposed_observer, breaks = self.observer_step(self.resistance, rx)
        self._copy_masked(self.resistance, proposed_observer, observe)
        breaks &= observe[..., None]
        activation = self.tape.activation_clock.index_select(0, self.index).squeeze(0)
        completed_clock = self.tape.geometry_clock.index_select(0, self.index).squeeze(
            0
        )
        listing_index = torch.arange(self.n, device=self.cash.device)
        prime_row = self.tape.market[1000][
            (completed_clock // 1000 - 1).clamp_min(0), listing_index
        ]
        prime_row = prime_row.unsqueeze(0).expand(self.b, -1, -1)
        prime = (
            (activation > 0).expand(self.b, -1)
            & (self.resistance.boundary_ms == 0)
            & (completed_clock > 0).expand(self.b, -1)
        )
        prime &= prime_row[..., MARKET_FIELDS.index("price_valid")] > 0
        initial, _ = self.observer_step(
            self.resistance,
            {
                **rx,
                "boundary_ms": completed_clock.expand(self.b, -1),
                "open_int": prime_row[..., MARKET_FIELDS.index("open_int")].to(
                    torch.int64
                ),
                "close_int": prime_row[..., MARKET_FIELDS.index("close_int")].to(
                    torch.int64
                ),
            },
        )
        self._copy_masked(self.resistance, initial, prime)
        low_observe = (
            active_before & (thirty["present"] > 0) & (boundary.remainder(30_000) == 0)
        )
        st["low_int"].copy_(torch.where(low_observe, thirty["low_int"], st["low_int"]))
        st["low_boundary_ms"].copy_(torch.where(low_observe, at, st["low_boundary_ms"]))
        st["low_valid"].copy_(
            torch.where(
                low_observe,
                (thirty["price_valid"] > 0) & (thirty["extremes_valid"] > 0),
                st["low_valid"],
            )
        )

        pending = self._pending()
        row_present = hundred["present"] > 0
        retire = source_before & (st["quantity"] == 0) & ~pending & row_present
        actually_held = retire & (st["initialized"] > 0)
        for name, value in (
            ("closed_boundary_ms", at),
            ("prior_entry_resistance_id", st["source_id"]),
            ("prior_position_high_int", st["position_high_int"]),
        ):
            st[name].copy_(torch.where(actually_held, value, st[name]))
        st["source_owned"].copy_(torch.where(retire, 0, st["source_owned"]))
        st["initialized"].copy_(torch.where(retire, 0, st["initialized"]))
        first_held = (
            source_before
            & (st["quantity"] > 0)
            & (st["initialized"] == 0)
            & row_present
        )
        st["initialized"].copy_(torch.where(first_held, 1, st["initialized"]))
        st["position_high_int"].copy_(
            torch.where(
                first_held,
                torch.round(st["source_ask"] * 10_000),
                st["position_high_int"],
            )
        )
        self.protection.stop.copy_(
            torch.where(first_held, st["source_stop"], self.protection.stop)
        )
        self.protection.target.copy_(
            torch.where(first_held, st["source_target"], self.protection.target)
        )
        self.protection.boundary_ms.copy_(
            torch.where(first_held, at.to(torch.int64), self.protection.boundary_ms)
        )
        held = (
            source_before & (st["quantity"] > 0) & (st["initialized"] > 0) & ~first_held
        )
        st["position_high_int"].copy_(
            torch.where(
                held & (hundred["price_valid"] > 0),
                torch.maximum(st["position_high_int"], hundred["high_int"]),
                st["position_high_int"],
            )
        )
        new_pending = held[..., None] & breaks & (self.pending_clock == 0)
        self.pending_clock.copy_(
            torch.where(new_pending, at[..., None], self.pending_clock)
        )
        self.pending_lower.copy_(torch.where(new_pending, lower, self.pending_lower))
        self.pending_mid.copy_(
            torch.where(new_pending, (lower + upper) / 2, self.pending_mid)
        )
        management = held & fresh & row_present
        old_accepted = self.protection.accepted.clone()
        # Stable sorts preserve lexical IDs as the final tie-breaker.
        order = torch.argsort(self.pending_mid, dim=-1, stable=True)
        ordered_clock = self.pending_clock.gather(-1, order)
        ordered_clock = torch.where(ordered_clock > 0, ordered_clock, float("inf"))
        order = order.gather(-1, torch.argsort(ordered_clock, dim=-1, stable=True))
        ids = (
            torch.arange(lower.shape[-1], device=lower.device)
            .expand_as(lower)
            .to(torch.int64)
        )
        break_id = torch.where(
            self.pending_clock.gather(-1, order) > 0, ids.gather(-1, order), 0
        )
        mid = torch.where(resistance_role > 0, (lower + upper) / 2, float("inf"))
        overhead_order = torch.argsort(mid, dim=-1, stable=True)
        overhead_id = torch.where(resistance_role > 0, ids, 0).gather(
            -1, overhead_order
        )
        px = {
            "boundary_ms": at.to(torch.int64),
            "bid": bid,
            "ask": ask,
            "tick": torch.full_like(bid, 0.01),
            "break_id": break_id,
            "break_lower": self.pending_lower.gather(-1, order),
            "overhead_id": overhead_id,
            "overhead_midpoint": mid.gather(-1, overhead_order),
            "price_bearing_bar": hundred["price_valid"] > 0,
            "low_int": st["low_int"],
            "low_boundary_ms": st["low_boundary_ms"].to(torch.int64),
            "low_valid": st["low_valid"] > 0,
        }
        proposed, _, _, _ = self.protection_step(self.protection, px)
        # Broker acknowledgement remains an engine contract even when the
        # optimizer changes policy operations. A policy cannot manufacture a
        # crossed bracket or erase protection by proposing an invalid price.
        price_grid = lambda value: torch.round(value * 10000)
        target_ack = (
            torch.isfinite(proposed.target)
            & (price_grid(proposed.target) >= price_grid(self.protection.target))
            & (price_grid(proposed.target) > price_grid(self.protection.stop))
        )
        proposed.target = torch.where(
            target_ack, proposed.target, self.protection.target
        )
        stop_ack = (
            torch.isfinite(proposed.stop)
            & (price_grid(proposed.stop) >= price_grid(self.protection.stop))
            & (
                price_grid(proposed.stop)
                < torch.minimum(price_grid(bid), price_grid(proposed.target))
            )
        )
        proposed.stop = torch.where(stop_ack, proposed.stop, self.protection.stop)
        proposed.applied_groups = torch.where(
            stop_ack, proposed.applied_groups, self.protection.applied_groups
        )
        self._copy_masked(self.protection, proposed, management)
        self.pending_clock.copy_(
            torch.where(
                management[..., None] | retire[..., None], 0, self.pending_clock
            )
        )
        return pending, management, breaks, old_accepted, lower, upper

    def _decisions(
        self,
        hundred,
        second,
        fact,
        at,
        bid,
        ask,
        fresh,
        pending,
        management,
        breaks,
        old_accepted,
        lower,
        upper,
        source_before,
    ):
        st = self.state
        # Atomic add gate consumes one current-boundary, newly confirmed break.
        selectable = breaks & ~old_accepted & self.protection.accepted
        selected_mid = torch.where(selectable, (lower + upper) / 2, float("inf")).amin(
            -1
        )
        zeros = torch.zeros_like(at)
        ax = {
            "boundary_ms": at.to(torch.int64),
            "break_boundary_ms": at.to(torch.int64),
            "break_is_resistance": torch.isfinite(selected_mid),
            "break_new": torch.isfinite(selected_mid),
            "break_accepted": torch.isfinite(selected_mid),
            "quantity": st["quantity"],
            "pending_exit": torch.zeros_like(pending),
            "pending_entry": pending,
            "pending_capital_request": torch.zeros_like(pending),
            "purchase_ordinal": (st["purchase_groups"] + 1).to(torch.int64),
            "current_purchase_groups": st["purchase_groups"].to(torch.int64),
            "bars_valid": (hundred["price_valid"] > 0)
            & (second["price_valid"] > 0)
            & (hundred["indicator_resolution_ms"] == 100)
            & (second["indicator_resolution_ms"] == 1000),
            "bar_100_boundary_ms": at.to(torch.int64),
            "bar_1s_boundary_ms": (at // 1000 * 1000).to(torch.int64),
            "macd_100_line": hundred["macd_line"],
            "macd_100_signal": hundred["macd_signal"],
            "macd_1s_line": second["macd_line"],
            "macd_1s_signal": second["macd_signal"],
            "open_1s_int": second["open_int"].to(torch.int64),
            "close_1s_int": second["close_int"].to(torch.int64),
            "close_100_int": hundred["close_int"].to(torch.int64),
            "break_midpoint": selected_mid,
            "quote_valid": fresh,
            "bid": bid,
            "ask": ask,
            "fresh_bid": bid,
            "fresh_ask": ask,
            "stop": self.protection.stop,
            "target": self.protection.target,
        }
        # Search arrays are evaluated as primitives, then Portfolio admission
        # folds shared reservations in the same ticker order as the app.
        ex = {
            **ax,
            **fact,
            "boundary_ms": at.to(torch.int64),
            "bid_int": hundred["bid_int"].to(torch.int64),
            "ask_int": hundred["ask_int"].to(torch.int64),
            "quote_age_us": hundred["quote_age_us"].to(torch.int64),
            "completed_entries": st["completed_entries"].to(torch.int64),
            "quantity": st["quantity"],
            "pending_entry": pending,
            "pending_exit": torch.zeros_like(pending),
            "pending_capital_request": torch.zeros_like(pending),
            "entry_permission": torch.ones_like(pending),
            "reentry_not_before_ms": zeros.to(torch.int64),
            "closed_boundary_ms": st["closed_boundary_ms"].to(torch.int64),
            "prior_entry_resistance_id": st["prior_entry_resistance_id"].to(
                torch.int64
            ),
            "prior_position_high_int": st["prior_position_high_int"].to(torch.int64),
            "previous_bar_close_int": hundred["previous_bar_close_int"].to(torch.int64),
            "current_bar_close_int": hundred["close_int"].to(torch.int64),
            "available_cash": self.cash[:, None].expand(self.b, self.n),
            "working_stop": self.protection.stop,
            "working_target": self.protection.target,
            "position_mark": st["mark"],
            "average_cost": st["average"],
        }
        if self.feature_bank is not None:
            extra = self.feature_bank.at(self.index, self.b)
            ex.update(extra)
            ax.update(extra)
        actions = {
            name: self.action_steps[name](ex, self.action_theta[name])
            for name in self.action_graphs
        }
        ex["stop"], ex["target"] = actions["initial_stop"], actions["initial_target"]
        valid_fraction = (
            torch.isfinite(actions["capital_fraction"])
            & (actions["capital_fraction"] > 0)
            & (actions["capital_fraction"] <= 1)
        )
        ax.update(
            {
                name: ex[name]
                for name in (
                    "available_cash",
                    "working_stop",
                    "working_target",
                    "position_mark",
                    "average_cost",
                )
            }
        )
        ax = {**ex, **ax}
        add = self.add_step(ax, self.add_theta) & management & valid_fraction
        for item in self.entry_graph.inputs:
            if self.feature_bank is not None and item.name in extra:
                # Optional source lanes use NaN for unavailable history. An
                # integer cast would turn NaN into a finite sentinel and allow
                # NOT to manufacture permission. Source price/count integers
                # fit the checked float64 feature representation.
                if item.kind == "bool":
                    value = ex[item.name]
                    ex[item.name] = torch.where(
                        torch.isfinite(value),
                        (value > 0).to(torch.float64),
                        float("nan"),
                    )
                continue
            if item.kind == "bool":
                ex[item.name] = ex[item.name] > 0
            elif item.kind == "integer":
                ex[item.name] = ex[item.name].to(torch.int64)
        executable = (
            fresh
            & torch.isfinite(actions["initial_stop"])
            & torch.isfinite(actions["initial_target"])
            & (actions["initial_stop"] > 0)
            & (torch.round(actions["initial_stop"] * 10000) < torch.round(bid * 10000))
            & (
                torch.round(actions["initial_target"] * 10000)
                > torch.round(ask * 10000)
            )
        )
        enter = (
            self.entry_step(ex, self.theta)
            & ~source_before
            & valid_fraction
            & executable
        )
        return add, enter, actions

    def _admit(self, listing, enter, add, ask, fact, boundary, actions):
        st = self.state
        initial_quantity = self._fund(
            listing,
            enter[:, listing]
            & torch.isfinite(actions["initial_target"][:, listing])
            & torch.isfinite(actions["initial_stop"][:, listing])
            & (
                torch.round(actions["initial_target"][:, listing] * 10000)
                > torch.round(ask[:, listing] * 10000)
            ),
            ask[:, listing],
            actions["initial_stop"][:, listing],
            actions["capital_fraction"][:, listing],
        )
        admitted = initial_quantity > 0
        # Retiring an old campaign keeps ticker-owned resistance state but
        # resets all position-owned identities and order/commission slots.
        for field in fields(self.books[listing]):
            buffer = getattr(self.books[listing], field.name)
            buffer.copy_(torch.where(admitted[:, None], 0, buffer))
        self._submit(
            listing,
            0,
            initial_quantity,
            ask[:, listing],
            actions["initial_stop"][:, listing],
            actions["initial_target"][:, listing],
            boundary,
        )
        for name, value in (
            ("source_owned", torch.ones_like(initial_quantity)),
            ("initialized", torch.zeros_like(initial_quantity)),
            ("source_ask", ask[:, listing]),
            ("source_stop", actions["initial_stop"][:, listing]),
            ("source_target", actions["initial_target"][:, listing]),
            ("source_id", fact["bos_support_level_id"][:, listing]),
            ("purchase_groups", torch.ones_like(initial_quantity)),
        ):
            st[name][:, listing].copy_(
                torch.where(admitted, value, st[name][:, listing])
            )
        for field in fields(self.protection):
            buffer = getattr(self.protection, field.name)[:, listing]
            value = float("inf") if field.name in ("pending_min", "earned_min") else 0
            mask = admitted if buffer.ndim == 1 else admitted[:, None]
            buffer.copy_(torch.where(mask, value, buffer))
        for group in (1, 2):
            desired = add[:, listing] & (st["purchase_groups"][:, listing] == group)
            quantity = self._fund(
                listing,
                desired,
                ask[:, listing],
                self.protection.stop[:, listing],
                actions["capital_fraction"][:, listing],
            )
            accepted = self._submit(
                listing,
                group,
                quantity,
                ask[:, listing],
                self.protection.stop[:, listing],
                self.protection.target[:, listing],
                boundary,
            )
            st["purchase_groups"][:, listing].add_(accepted.to(torch.float64))
            add[:, listing] &= ~accepted

    def tick(self):
        st = self.state
        boundary = (self.index + 1) * 100
        at = boundary.reshape(()).expand(self.b, self.n)
        hundred, second, thirty = (
            self._market(100),
            self._market(1000),
            self._market(30_000),
        )
        facts_row = self._slice(self.tape.facts, self.index)
        fact = {name: facts_row[..., i] for i, name in enumerate(FACT_FIELDS)}
        source_before = st["source_owned"] > 0
        pending_before = self._pending()
        active_before = (st["quantity"] > 0) | pending_before
        fresh = (
            (hundred["quote_valid"] > 0)
            & (hundred["bid_int"] > 0)
            & (hundred["ask_int"] >= hundred["bid_int"])
            & (hundred["quote_age_us"] >= 0)
            & (hundred["quote_age_us"] <= 1_000_000)
        )
        bid, ask = hundred["bid_int"] / 10_000, hundred["ask_int"] / 10_000
        st["last_bid"].copy_(torch.where(fresh, bid, st["last_bid"]))
        st["last_ask"].copy_(torch.where(fresh, ask, st["last_ask"]))
        mark = torch.where(
            hundred["price_valid"] > 0,
            hundred["close_int"] / 10_000,
            torch.where(fresh, (bid + ask) / 2, 0),
        )
        st["mark"].copy_(torch.where(mark > 0, mark, st["mark"]))

        # Native adaptive-urgent repricing precedes this interval's broker match.
        # Strategy 1 has no later quote-age check in this repricer; it retains
        # the last permitted execution snapshot when no new NBBO exists.
        global_event = ((hundred["present"] > 0) & active_before).any(-1) | fact[
            "candidate_valid"
        ].any(-1)
        for listing, book in enumerate(self.books):
            self.reprice_step(book, st["last_ask"][:, listing], global_event)

        source = self.tape.broker[self.broker_ms]
        interval_index = self.index.div(self.broker_ms // 100, rounding_mode="floor")
        row = self._slice(source["market"], interval_index)
        broker_row = {name: row[..., i] for i, name in enumerate(MARKET_FIELDS)}
        pointers = source["pointers"].index_select(0, interval_index).squeeze(0)
        offsets = torch.arange(source["maximum_prices"], device=self.cash.device)
        chosen = pointers[:, :1] + offsets[None, :]
        price_rows = source["prices"][chosen.clamp_max(source["prices"].shape[0] - 1)]
        price_rows = torch.where(
            (offsets[None, :] < pointers[:, 1:2])[..., None], price_rows, 0
        )
        broker_tick = boundary.remainder(self.broker_ms) == 0
        events = []
        for listing, book in enumerate(self.books):
            x = {name: broker_row[name][:, listing] for name in MARKET_FIELDS}
            x.update(
                {
                    "boundary_ms": boundary.reshape(()).expand(self.b),
                    "interval_start_ms": (boundary - self.broker_ms)
                    .reshape(())
                    .expand(self.b),
                    "present": (x["present"] > 0) & broker_tick.reshape(()),
                    "quote_valid": (x["quote_valid"] > 0)
                    & (x["quote_age_us"] >= 0)
                    & (x["quote_age_us"] <= 1_000_000),
                    "bid": x["bid_int"] / 10_000,
                    "ask": x["ask_int"] / 10_000,
                    "low": torch.where(
                        x["extremes_valid"] > 0, x["low_int"] / 10_000, 0
                    ),
                    "price_int": price_rows[listing, :, 0].expand(self.b, -1),
                    "price_volume": price_rows[listing, :, 1].expand(self.b, -1),
                }
            )
            previously_filled = book.group_acquired[:, 0] > 0
            cash, qty, avg, risk, alloc_avg, realized, fees, fills = self.broker_step(
                book,
                x,
                self.cash,
                st["quantity"][:, listing],
                st["average"][:, listing],
                st["allocated_risk"][:, listing],
                st["allocated_average"][:, listing],
                1.0,
                0.25,
                5.0,
            )
            self.cash.copy_(cash)
            self.realized.add_(realized)
            self.fees.add_(fees)
            st["quantity"][:, listing].copy_(qty)
            st["average"][:, listing].copy_(avg)
            st["allocated_risk"][:, listing].copy_(risk)
            st["allocated_average"][:, listing].copy_(alloc_avg)
            st["completed_entries"][:, listing].add_(
                (~previously_filled & (book.group_acquired[:, 0] > 0)).to(torch.float64)
            )
            events.append(fills)
        self._record(torch.stack(events, 1), boundary)

        pending, management, breaks, old_accepted, lower, upper = self._manage(
            hundred,
            second,
            thirty,
            at,
            boundary,
            source_before,
            active_before,
            fresh,
            bid,
            ask,
        )
        for listing, book in enumerate(self.books):
            self.amend_step(
                book,
                management[:, listing],
                self.protection.target[:, listing],
                self.protection.stop[:, listing],
            )

        add, enter, actions = self._decisions(
            hundred,
            second,
            fact,
            at,
            bid,
            ask,
            fresh,
            pending,
            management,
            breaks,
            old_accepted,
            lower,
            upper,
            source_before,
        )
        self._prepare_funding()
        for listing in range(self.n):
            self._admit(listing, enter, add, ask, fact, boundary, actions)
        self.index.add_(1)

    def compile(self):
        started = perf_counter()
        # Topology specialization is bounded by N listings and three purchase
        # groups. This is setup compilation, never data-driven recompilation.
        torch._dynamo.config.recompile_limit = max(64, self.n * 3 + 8)
        # Compile reusable mathematical kernels rather than inlining ten
        # aliased books into one enormous functionalization graph. The complete
        # causal sequence is subsequently captured; there is no host dispatch
        # or tensor-to-host scalar decision inside a replayed graph block.
        self.observer_step = self.resistance_policy
        self.protection_step = self.protection_policy
        # Lower all named state/evidence fields once before Dynamo compilation.
        # Replay executes the reconstructed primitive arrays, never these
        # reference Python reducers. Default thresholds reproduce Strategy 1.
        with torch.inference_mode():
            self.tick()
        self.reset()
        self.broker_step = torch.compile(match_listing, fullgraph=True)
        self.entry_step = torch.compile(self.entry_graph.evaluate, fullgraph=True)
        self.add_step = torch.compile(self.add_graph.evaluate, fullgraph=True)
        self.action_steps = {
            name: torch.compile(graph.evaluate, fullgraph=True)
            for name, graph in self.action_graphs.items()
        }
        self.reprice_step = torch.compile(reprice_book, fullgraph=True)
        self.amend_step = torch.compile(amend_book, fullgraph=True)
        self._manage = torch.compile(self._manage, fullgraph=True)
        self._decisions = torch.compile(self._decisions, fullgraph=True)
        self._prepare_funding = torch.compile(self._prepare_funding, fullgraph=True)
        self._admit = torch.compile(self._admit, fullgraph=True)
        self._record = torch.compile(self._record, fullgraph=True)
        self._copy_masked = torch.compile(self._copy_masked, fullgraph=True)
        self.compiled_step = self.tick
        with torch.inference_mode():
            self.compiled_step()
        if self.cash.is_cuda:
            torch.cuda.synchronize()
        self.compile_seconds = perf_counter() - started
        self.reset()
        if self.cash.is_cuda:
            started = perf_counter()
            stream = torch.cuda.Stream(device=self.cash.device)
            stream.wait_stream(torch.cuda.current_stream(self.cash.device))
            with torch.cuda.stream(stream):
                for _ in range(3):
                    self.reset()
                    self.compiled_step()
            torch.cuda.current_stream(self.cash.device).wait_stream(stream)
            self.reset()
            stream.wait_stream(torch.cuda.current_stream(self.cash.device))
            self.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph, stream=stream):
                for _ in range(self.graph_steps):
                    self.compiled_step()
            torch.cuda.synchronize(self.cash.device)
            self.capture_seconds = perf_counter() - started
            self.reset()

    def run(self, *, slots=None, progress=print):
        """Measured replay only; setup/transfer/compile costs remain separate."""
        slots = self.tape.through_ms // 100 if slots is None else slots
        if not 1 <= slots <= self.tape.through_ms // 100:
            raise ValueError("Replay prefix exceeds the resident causal tape")
        self.reset()
        step = self.compiled_step or self.tick
        if self.cash.is_cuda:
            torch.cuda.synchronize()
        started, last = perf_counter(), perf_counter()
        with torch.inference_mode():
            blocks = slots // self.graph_steps if self.graph else slots
            for tick in range(blocks):
                if self.graph:
                    self.graph.replay()
                else:
                    step()
                if perf_counter() - last >= 30:
                    progress(
                        f"Replay {self.broker_ms}ms broker: {(tick + 1) * (self.graph_steps if self.graph else 1):,}/{slots:,} strategy slots"
                    )
                    last = perf_counter()
            if self.graph:
                for _ in range(slots % self.graph_steps):
                    step()
        if self.cash.is_cuda:
            torch.cuda.synchronize()
        elapsed = perf_counter() - started
        unrealized = (
            (self.state["mark"] - self.state["average"]) * self.state["quantity"]
        ).sum(-1)
        return {
            "broker_ms": self.broker_ms,
            "strategy_ms": 100,
            "strategy_slots": slots,
            "replay_seconds": elapsed,
            "compile_seconds": self.compile_seconds,
            "capture_seconds": self.capture_seconds,
            "gross_realized": self.realized.cpu().tolist(),
            "unrealized": unrealized.cpu().tolist(),
            "fees": self.fees.cpu().tolist(),
            "net_pnl": (self.realized + unrealized - self.fees).cpu().tolist(),
            "cash": self.cash.cpu().tolist(),
            "fill_count": self.fill_count.cpu().flatten().tolist(),
            "open_quantities": self.state["quantity"].cpu().tolist(),
            "open_averages": self.state["average"].cpu().tolist(),
            "open_marks": self.state["mark"].cpu().tolist(),
            "tickers": self.tape.tickers,
        }
