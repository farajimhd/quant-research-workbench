"""A fixed-shape causal account engine, vectorized over candidates and listings.

Python advances the common clock; every market/account calculation is Torch.
There are no CPU tensor reads, dataframe plans, transfers, or database calls in
the tick function. CUDA graphs can launch the complete tick as one graph replay.
"""

from math import isfinite
from time import perf_counter

import torch

from research.vectorized_backtest.v1.strategy_encoding.config import Broker
from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError

from .compiler import TorchStrategy
from .data import TensorTape


class ReplayRunner:
    """Reusable runner for one graph topology and a [B,P] parameter batch.

    Integer quantities/orders [B,N] stay separate from float64 accounting [B,N].
    Cash, fees, realized P&L and drawdown [B] are reduced across the listing axis
    only: candidate accounts never share cash or simulated liquidity capacity.
    """

    def __init__(
        self,
        strategy: TorchStrategy,
        tape: TensorTape,
        broker=None,
        *,
        values=None,
        backend="eager",
        max_state_gib=1.0,
        graph_steps=1,
    ):
        self.strategy, self.tape = strategy, tape
        self.broker = Broker() if broker is None else broker
        b = self.broker
        if not all(isfinite(x) for x in vars(b).values()) or not (
            b.initial_cash > 0
            and 0 <= b.participation <= 1
            and 0 <= b.fee_bps < 10000
            and 0 < b.initial_stop_return < 1
            and b.initial_target_return > 0
            and b.drawdown_weight >= 0
        ):
            raise EncodingError("Invalid broker/objective contract")
        if backend not in {"eager", "compile", "cudagraph", "compiled_graph"}:
            raise EncodingError("Unknown Torch execution backend")
        if type(graph_steps) is not int or not 1 <= graph_steps <= 128:
            raise EncodingError("graph_steps must be an integer in [1,128]")
        if graph_steps != 1 and backend not in {"cudagraph", "compiled_graph"}:
            raise EncodingError("Multi-tick capture requires a CUDA graph backend")
        if backend in {"cudagraph", "compiled_graph"} and tape.device.type != "cuda":
            raise EncodingError(
                "CUDA graphs require an explicitly selected CUDA device"
            )
        prepared_inputs = {f.label: f for f in tape.prepared.dependencies}
        if any(
            f.source != "state"
            and (prepared_inputs.get(f.label) != f or f.label not in tape.labels)
            for f in strategy.dependencies
        ):
            raise EncodingError("Policy lies outside the prepared envelope")
        if any(
            (field.source, field.resolution_ms) not in tape.windows
            or field.label
            not in tape.windows[(field.source, field.resolution_ms)].labels
            for _, _, field, _, _ in strategy.source_windows
        ):
            raise EncodingError("Source-window dependencies require a new tensor tape")
        state_fields = {
            "quantity",
            "remaining",
            "average_entry",
            "stop",
            "target",
            "cash",
            "watchlisted",
        }
        if any(
            f.source == "state" and f.column not in state_fields
            for f in strategy.dependencies
        ):
            raise EncodingError("Unsupported account input contract")
        if (
            strategy.catalog.group_columns != ("listing_id", "session_date")
            or strategy.catalog.clock_column != "time_us"
        ):
            raise EncodingError(
                "Session replay requires the listing/session/UTC clock contract"
            )
        self.theta = strategy.parameters(
            strategy.program.values if values is None else values, tape.device
        )
        self.candidate_fingerprints = [strategy.fingerprint] * self.theta.shape[0]
        self.batch = self.theta.shape[0]
        self.listings = len(tape.listing_ids)
        self.slots = tape.market.shape[0]
        self.backend = backend
        self.graph_steps = min(graph_steps, self.slots)
        state_bytes = (
            self.slots * self.batch * 4
            + self.batch * self.listings * 10
            + sum(size * operands for size, operands in strategy.temporal_sizes)
            * self.batch
            * self.listings
        ) * 8
        if (
            not isfinite(max_state_gib)
            or max_state_gib <= 0
            or state_bytes > max_state_gib * 1024**3
        ):
            raise MemoryError(
                "Batch/account/history buffers exceed the state memory guard"
            )
        self.shape = (self.batch, self.listings)
        device = tape.device
        self.quantity = torch.zeros(self.shape, dtype=torch.int64, device=device)
        self.side = torch.zeros_like(self.quantity)
        self.remaining = torch.zeros_like(self.quantity)
        self.submitted = torch.zeros_like(self.quantity)
        self.cost = torch.zeros(self.shape, dtype=torch.float64, device=device)
        self.stop = torch.zeros_like(self.cost)
        self.target = torch.zeros_like(self.cost)
        self.mark = torch.zeros_like(self.cost)
        self.cash = torch.full(
            (self.batch,), b.initial_cash, dtype=torch.float64, device=device
        )
        self.realized = torch.zeros_like(self.cash)
        self.fees = torch.zeros_like(self.cash)
        self.filled = torch.zeros(self.batch, dtype=torch.int64, device=device)
        self.peak = torch.full_like(self.cash, b.initial_cash)
        self.drawdown = torch.zeros_like(self.cash)
        self.index = torch.zeros((), dtype=torch.int64, device=device)
        self.observation_count = torch.zeros_like(self.index)
        self.history = strategy.history(self.batch, self.listings, device)
        # [T,B,4]: cash, realized P&L, equity, market value, including warm-up.
        self.accounts = torch.zeros(
            (self.slots, self.batch, 4), dtype=torch.float64, device=device
        )
        self.market_labels = {label: i for i, label in enumerate(tape.labels)}
        self.compile_seconds = self.capture_seconds = 0.0
        self.step = self._tick
        self.graph = None
        self.tail_graph = None
        self.set_parameters(strategy.program.values if values is None else values)
        self._prepare_backend()

    def set_parameters(self, values):
        """Change Theta between runs while retaining compiled/captured pointers.

        Batch shape is fixed. Thresholds and source-bar counts can vary; changes
        to instructions or observation-ring sizes require a new runner.
        """
        from dataclasses import replace

        from .compiler import compile_strategy

        checked = self.strategy.parameters(values, self.tape.device)
        if checked.shape != self.theta.shape:
            raise EncodingError("Parameter batch shape changed; create a new runner")
        rows = checked.detach().cpu().tolist()
        self.candidate_fingerprints = [
            compile_strategy(
                replace(self.strategy.program, values=tuple(row)), self.strategy.catalog
            ).fingerprint
            for row in rows
        ]
        self.theta.copy_(checked)

    def reset(self):
        """Restore existing buffers in place; captured device pointers stay valid."""
        for tensor in (
            self.quantity,
            self.side,
            self.remaining,
            self.submitted,
            self.cost,
            self.stop,
            self.target,
            self.mark,
            self.realized,
            self.fees,
            self.filled,
            self.drawdown,
            self.index,
            self.observation_count,
            self.accounts,
        ):
            tensor.zero_()
        self.cash.fill_(self.broker.initial_cash)
        self.peak.fill_(self.broker.initial_cash)
        for buffers in self.history:
            for buffer in buffers:
                buffer.fill_(float("nan"))

    def _prepare_backend(self):
        """Compilation/capture are one-time costs, reported outside warm replay.

        Backends are explicit: compilation errors propagate; there is no eager
        or CPU fallback hidden inside a reported compiled/CUDA benchmark.
        """
        with torch.inference_mode():
            if self.backend in {"compile", "compiled_graph"}:
                started = perf_counter()
                self.step = torch.compile(self._tick, fullgraph=True, mode="default")
                self.step()
                if self.tape.device.type == "cuda":
                    torch.cuda.synchronize(self.tape.device)
                self.compile_seconds = perf_counter() - started
                self.reset()
            if self.backend in {"cudagraph", "compiled_graph"}:
                started = perf_counter()
                stream = torch.cuda.Stream(device=self.tape.device)
                stream.wait_stream(torch.cuda.current_stream(self.tape.device))
                with torch.cuda.stream(stream):
                    for _ in range(3):
                        self.reset()
                        self.step()
                torch.cuda.current_stream(self.tape.device).wait_stream(stream)
                self.reset()
                stream.wait_stream(torch.cuda.current_stream(self.tape.device))
                self.graph = torch.cuda.CUDAGraph()
                with torch.cuda.graph(self.graph, stream=stream):
                    for _ in range(self.graph_steps):
                        self.step()
                # A separate exact-size remainder preserves the final boundary;
                # no padded tick can read past the resident tape or trade later.
                remainder = self.slots % self.graph_steps
                if remainder:
                    torch.cuda.current_stream(self.tape.device).wait_stream(stream)
                    self.reset()
                    stream.wait_stream(torch.cuda.current_stream(self.tape.device))
                    self.tail_graph = torch.cuda.CUDAGraph()
                    with torch.cuda.graph(self.tail_graph, stream=stream):
                        for _ in range(remainder):
                            self.step()
                torch.cuda.synchronize(self.tape.device)
                self.capture_seconds = perf_counter() - started
                self.reset()

    def _tick(self):
        """Completed fills → observation/proposals → new orders → marked equity.

        Scalars such as clock, cadence masks and shared cash stay on device.
        Order eligibility uses the broker interval START, not its closing clock.
        """
        tape, broker = self.tape, self.broker
        row = self.index.reshape(1)
        clock = tape.first_us + self.index * tape.step_us
        broker_us = tape.prepared.config.broker_ms * 1000
        strategy_us = tape.prepared.config.strategy_ms * 1000
        broker_tick = (clock > tape.start_us) & (
            (clock - tape.origin_us).remainder(broker_us) == 0
        )
        observe = (clock - tape.origin_us).remainder(strategy_us) == 0
        decision = observe & (clock >= tape.start_us) & (clock < tape.end_us)
        market = tape.market.index_select(0, row).squeeze(0)  # [N,F]
        execution = tape.broker.index_select(0, row).squeeze(0)  # [N,6]
        close, valid, volume, vwap, reference, reference_valid = execution.unbind(
            dim=-1
        )
        active = (self.quantity > 0) | (self.remaining > 0)
        valid_price = (valid == 1) & broker_tick & active
        mark = torch.where(valid_price, close, self.mark)
        capacity = torch.where(
            valid_price & (self.submitted <= clock - broker_us) & (volume > 0),
            torch.floor(volume * broker.participation),
            0.0,
        ).to(torch.int64)
        average = torch.where(
            self.quantity > 0, self.cost / self.quantity.clamp_min(1), 0.0
        )
        sell = torch.where(
            self.side == -1,
            torch.minimum(torch.minimum(self.quantity, self.remaining), capacity),
            0,
        )
        candidate = torch.where(
            self.side == 1, torch.minimum(self.remaining, capacity), 0
        )
        sale = sell * vwap * (1 - broker.fee_bps / 10000)
        need = candidate * vwap * (1 + broker.fee_bps / 10000)
        total_need = need.sum(dim=1)
        # [B,1] broadcasts each account's pro-rata allocation over its listings.
        scale = torch.where(
            total_need > 0,
            ((self.cash + sale.sum(dim=1)) / total_need.clamp_min(1e-300)).clamp_max(1),
            1.0,
        )
        buy = torch.floor(candidate * scale[:, None]).to(torch.int64)
        new_quantity = self.quantity + buy - sell
        new_remaining = self.remaining - buy - sell
        new_cost = (
            self.cost + buy * vwap * (1 + broker.fee_bps / 10000) - sell * average
        )
        self.cash.add_((sale - buy * vwap * (1 + broker.fee_bps / 10000)).sum(dim=1))
        self.realized.add_(
            (sell * (vwap * (1 - broker.fee_bps / 10000) - average)).sum(dim=1)
        )
        self.fees.add_(((buy + sell) * vwap * broker.fee_bps / 10000).sum(dim=1))
        self.filled.add_((buy + sell).sum(dim=1))
        first_fill = (self.quantity == 0) & (buy > 0)
        stop = torch.where(
            first_fill, vwap * (1 - broker.initial_stop_return), self.stop
        )
        target = torch.where(
            first_fill, vwap * (1 + broker.initial_target_return), self.target
        )
        protect = (
            valid_price
            & (new_quantity > 0)
            & (self.side != -1)
            & ((mark <= stop) | (mark >= target))
        )
        side = torch.where(protect, -1, self.side)
        new_remaining = torch.where(protect, new_quantity, new_remaining)
        submitted = torch.where(protect, clock, self.submitted)
        active = (new_quantity > 0) | (new_remaining > 0)
        # Dropped Polars state rows become zeroed fixed slots, not stale orders.
        self.quantity.copy_(torch.where(active, new_quantity, 0))
        self.remaining.copy_(torch.where(active, new_remaining, 0))
        self.cost.copy_(torch.where(active, new_cost, 0.0))
        self.side.copy_(torch.where(active, side, 0))
        self.submitted.copy_(torch.where(active, submitted, 0))
        self.stop.copy_(torch.where(active, stop, 0.0))
        self.target.copy_(torch.where(active, target, 0.0))
        self.mark.copy_(torch.where(active, mark, 0.0))

        admitted = tape.admitted <= clock  # [N], broadcast independently to B.
        state_inputs = {
            "quantity": self.quantity.to(torch.float64),
            "remaining": self.remaining.to(torch.float64),
            "average_entry": torch.where(
                self.quantity > 0, self.cost / self.quantity.clamp_min(1), 0.0
            ),
            "stop": self.stop,
            "target": self.target,
            "cash": self.cash[:, None].expand(self.shape),
            "watchlisted": admitted.to(torch.float64).expand(self.shape),
        }
        inputs = {}
        for feature in self.strategy.dependencies:
            inputs[feature.label] = (
                state_inputs[feature.column]
                if feature.source == "state"
                else market[:, self.market_labels[feature.label]].expand(self.shape)
            )
        # Constant-only programs still need an explicitly shaped tensor context.
        if not inputs:
            inputs[-999999] = self.cost
        source_windows = {
            index: tape.windows[(feature.source, feature.resolution_ms)].window(
                row, clock, feature.label, maximum
            )
            for index, _, feature, _, maximum in self.strategy.source_windows
        }
        actions = self.strategy.evaluate(
            inputs,
            self.theta,
            self.history,
            self.observation_count,
            observe,
            source_windows,
        )
        self.observation_count.add_(observe.to(torch.int64))
        allowed = decision & admitted
        enter = (actions[:, :, 0] == 1) & allowed
        exit_ = (actions[:, :, 2] == 1) & allowed
        add = (actions[:, :, 4] == 1) & allowed
        ref_ok = (reference_valid == 1) & (reference > 0)
        sell_request = exit_ & (self.quantity > 0) & (self.side != -1)
        buy_request = (
            ref_ok
            & (self.remaining == 0)
            & (((self.quantity == 0) & enter) | ((self.quantity > 0) & add))
        )
        fraction = torch.where(self.quantity == 0, actions[:, :, 1], actions[:, :, 5])
        requested = torch.floor(
            torch.nan_to_num(
                self.cash[:, None] * fraction / reference,
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            )
        ).to(torch.int64)
        requested = torch.where(ref_ok, requested, 0)
        stop = torch.where(
            (actions[:, :, 6] == 1) & allowed & (self.quantity > 0),
            actions[:, :, 7],
            self.stop,
        )
        target = torch.where(
            (actions[:, :, 8] == 1) & allowed & (self.quantity > 0),
            actions[:, :, 9],
            self.target,
        )
        side = torch.where(sell_request, -1, torch.where(buy_request, 1, self.side))
        remaining = torch.where(
            sell_request,
            self.quantity,
            torch.where(buy_request, requested, self.remaining),
        )
        submitted = torch.where(sell_request | buy_request, clock, self.submitted)
        mark = torch.where(
            (self.quantity == 0) & ref_ok & allowed, reference, self.mark
        )
        active = (self.quantity > 0) | (remaining > 0)
        self.side.copy_(torch.where(active, side, 0))
        self.remaining.copy_(torch.where(active, remaining, 0))
        self.submitted.copy_(torch.where(active, submitted, 0))
        self.cost.copy_(torch.where(active, self.cost, 0.0))
        self.stop.copy_(torch.where(active, stop, 0.0))
        self.target.copy_(torch.where(active, target, 0.0))
        self.mark.copy_(torch.where(active, mark, 0.0))
        value = (self.quantity * self.mark).sum(dim=1)
        equity = self.cash + value
        self.peak.copy_(torch.maximum(self.peak, equity))
        self.drawdown.copy_(torch.maximum(self.drawdown, 1 - equity / self.peak))
        account = torch.stack(
            (self.cash, self.realized, equity, value), dim=-1
        )  # [B,4]
        self.accounts.index_copy_(0, row, account.unsqueeze(0))
        self.index.add_(1)

    def run(self, *, progress=None):
        """Reset and execute the full session. Outputs remain on the selected device.

        Timing includes reset and final synchronization, excludes compilation,
        capture, input alignment and transfer. Export synchronizes only afterward.
        """
        device = self.tape.device
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        started = perf_counter()
        with torch.inference_mode():
            self.reset()
            launches = (
                self.slots if self.graph is None else self.slots // self.graph_steps
            )
            cadence = max(1, 3600_000_000 // self.tape.step_us)
            for step in range(launches):
                if self.graph is None:
                    self.step()
                else:
                    self.graph.replay()
                completed = step if self.graph is None else step * self.graph_steps
                previous = completed - (1 if self.graph is None else self.graph_steps)
                if progress and (
                    step == 0 or completed // cadence > previous // cadence
                ):
                    progress(
                        {
                            "stage": "torch_replay",
                            "completed_slots": completed,
                            "total_slots": self.slots,
                        }
                    )
            if self.tail_graph is not None:
                self.tail_graph.replay()
            if device.type == "cuda":
                torch.cuda.synchronize(device)
        wall = perf_counter() - started
        first_account = (self.tape.start_us - self.tape.first_us) // self.tape.step_us
        accounts = self.accounts[first_account:]
        returns = accounts[-1, :, 2] / self.broker.initial_cash - 1
        objective = returns - self.broker.drawdown_weight * self.drawdown
        return {
            "objective": objective.detach().clone(),
            "return_fraction": returns.detach().clone(),
            "max_drawdown": self.drawdown.clone(),
            "fees": self.fees.clone(),
            "filled_shares": self.filled.clone(),
            "accounts": accounts.clone(),
            "state": {
                name: tensor.clone()
                for name, tensor in (
                    ("quantity", self.quantity),
                    ("book_cost", self.cost),
                    ("side", self.side),
                    ("remaining", self.remaining),
                    ("submitted_us", self.submitted),
                    ("stop", self.stop),
                    ("target", self.target),
                    ("mark", self.mark),
                )
            },
            "open_positions": (self.quantity > 0).sum(dim=1),
            "pending_orders": (self.remaining > 0).sum(dim=1),
            "wall_seconds": wall,
            "compile_seconds": self.compile_seconds,
            "capture_seconds": self.capture_seconds,
            "backend": self.backend,
            "graph_steps": self.graph_steps,
            "graph_launches": launches + (self.tail_graph is not None)
            if self.graph is not None
            else 0,
            "batch": self.batch,
            "strategy_calls": (
                (self.tape.end_us - self.tape.start_us)
                // (self.tape.prepared.config.strategy_ms * 1000)
            )
            if self.listings
            else 0,
            "broker_slots": (self.tape.end_us - self.tape.start_us)
            // (self.tape.prepared.config.broker_ms * 1000),
            "source_key": self.tape.prepared.source_key,
            "program_fingerprint": self.strategy.fingerprint,
            "candidate_fingerprints": self.candidate_fingerprints,
            "candidate_parameters": self.theta.clone(),
        }
