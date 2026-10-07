"""V2 portfolio mechanics with v3 candidate/value/atomic-rule tensor buffers."""

import json
from dataclasses import asdict, replace
from hashlib import sha256

import numpy as np
import torch

from .genome import NAMES, POLICY_FIELDS
from .rules import ATOMS, HISTORY, evaluate
from .runner import SqueezeRunner


class SearchRunner(SqueezeRunner):
    def __init__(
        self,
        tape,
        space,
        population,
        *,
        backend="eager",
        precompute_rules=False,
        **kwargs,
    ):
        rows = space.validate(population)
        decoded = space.decode(rows)
        self.space, self.numeric = space, None
        self.precompute_rules = precompute_rules
        # Allocate shared source histories once at grammar caps. Candidate masks
        # choose completed lookbacks; no gene changes a captured tensor shape.
        settings = replace(
            decoded[0].settings,
            adaptive_window=32,
            swing_left_seconds=5,
            swing_right_seconds=5,
            retest_lookback_seconds=12,
            momentum_lookback_seconds=12,
            attention_lookback_seconds=12,
        )
        if getattr(self,'specialize',False):
            adaptive,left,right,momentum,attention=self.execution_key[2]
            settings=replace(settings,adaptive_window=adaptive,
                             swing_left_seconds=left,swing_right_seconds=right,
                             momentum_lookback_seconds=momentum,attention_lookback_seconds=attention)
        if len({space.group_key(row) for row in rows}) != 1:
            raise ValueError(
                "Different history allocation shapes require separate groups"
            )
        super().__init__(
            tape, [d.candidate for d in decoded], settings, backend=backend, **kwargs
        )
        self.swing_low = torch.full(
            (self.b, self.n), float("nan"), dtype=torch.float64, device=tape.device
        )
        self.numeric = torch.empty(
            (self.b, len(POLICY_FIELDS)), dtype=torch.float64, device=tape.device
        )
        # Column-contiguous device policy buffers avoid a strided candidate
        # lookup inside wide ticker/position broadcasts. Updated once per
        # population, preserving captured pointers and the public [B,P] tensor.
        self.numeric_columns = torch.empty(
            (len(POLICY_FIELDS), self.b), dtype=torch.float64, device=tape.device
        )
        self.clauses = torch.empty(
            (self.b, 4, 6), dtype=torch.float64, device=tape.device
        )
        self.connectors = torch.empty(
            (self.b, 3), dtype=torch.float64, device=tape.device
        )
        self.rule_history = torch.full(
            (HISTORY, self.n, len(ATOMS)),
            float("nan"),
            dtype=torch.float64,
            device=tape.device,
        )
        self.current_atoms = torch.full(
            (self.n, len(ATOMS)), float("nan"), dtype=torch.float64, device=tape.device
        )
        self._state_names.extend(("rule_history", "current_atoms"))
        self.set_genomes(rows)
        self.reset()
        if precompute_rules:
            from .rule_precompute import RuleCompiler

            self.rule_compiler = RuleCompiler(self)

    def set_genomes(self, population):
        rows = self.space.validate(population)
        if len(rows) != self.b:
            raise ValueError("Changing candidate batch requires a new runner")
        if hasattr(self, "genomes") and any(
            self.space.group_key(row) != self.space.group_key(self.genomes[0])
            for row in rows
        ):
            raise ValueError("Changing batch/history shapes requires a new runner")
        decoded = self.space.decode(rows)
        self.set_candidates([d.candidate for d in decoded])
        self.numeric.copy_(
            torch.as_tensor(
                rows[:, self.space.policy_start : self.space.rules_start],
                device=self.tape.device,
            )
        )
        self.numeric_columns.copy_(self.numeric.T)
        self.clauses.copy_(
            torch.as_tensor(
                rows[:, self.space.rules_start : self.space.connectors_start].reshape(
                    self.b, 4, 6
                ),
                device=self.tape.device,
            )
        )
        self.connectors.copy_(
            torch.as_tensor(
                rows[:, self.space.connectors_start :], device=self.tape.device
            )
        )
        self.genomes = np.array(rows, copy=True)

    def _masked_reduce(self, ring, name, reduction="mean"):
        window = self._value(name, 3)
        selected = torch.arange(len(ring), device=ring.device)[None, :, None] < window
        values = ring[None].expand(self.b, -1, -1)
        if reduction == "maximum":
            # NaN inside a selected window stays NaN and fails its entry gate.
            return torch.where(selected, values, -float("inf")).amax(1)
        return torch.where(selected, values, 0).sum(1) / window.squeeze(1)

    def _recent_high(self):
        return self._masked_reduce(
            self.price_ring, "retest_lookback_seconds", "maximum"
        )

    def _momentum_close(self):
        indices = self._value("momentum_lookback_seconds", 2).to(torch.int64) - 1
        return (
            self.close_ring.T[None]
            .expand(self.b, -1, -1)
            .gather(-1, indices[..., None].expand(self.b, self.n, 1))
            .squeeze(-1)
        )

    def _attention_mean(self):
        return self._masked_reduce(self.attention_ring, "attention_lookback_seconds")

    def _average_move(self):
        return self._masked_reduce(self.movement_ring, "adaptive_window")

    def _swing_level(self):
        return self.swing_low

    def _confirm_swing(self):
        right = self._value("swing_right_seconds", 2).to(torch.int64)
        length = (
            self._value("swing_left_seconds", 3)
            + self._value("swing_right_seconds", 3)
            + 1
        )
        selected = (
            torch.arange(len(self.low_ring), device=self.low_ring.device)[None, :, None]
            < length
        )
        values = self.low_ring[None].expand(self.b, -1, -1)
        pivot = (
            self.low_ring.T[None]
            .expand(self.b, -1, -1)
            .gather(-1, right[..., None].expand(self.b, self.n, 1))
            .squeeze(-1)
        )
        minimum = torch.where(selected, values, float("inf")).amin(1)
        confirmed = (~selected | torch.isfinite(values)).all(1) & (pivot == minimum)
        self.swing_low.copy_(torch.where(confirmed, pivot, self.swing_low))

    def _value(self, name, rank):
        if self.numeric is not None and name in NAMES:
            return self.numeric_columns[NAMES.index(name)].reshape(
                (self.b,) + (1,) * (rank - 1)
            )
        return getattr(self.settings, name)

    def _remainder_value(self, name):
        # Native mutation performs this broadcast safely. Keep policies compact
        # and contiguous rather than allocating eight full order-shaped copies.
        return self.numeric_columns[NAMES.index(name), :, None]

    def reset(self):
        super().reset()
        if hasattr(self, "rule_history"):
            self.rule_history.fill_(float("nan"))
            self.current_atoms.fill_(float("nan"))

    def _entry_filter(self, now, close):
        if self.precompute_rules:
            return self.rule_gate.index_select(0, self.index.reshape(1)).squeeze(0)
        bid, ask = self._row("bid"), self._row("ask")
        vwap = self._row("vwap")
        observed = self._row("observed")
        current = torch.where(observed, close, float("nan"))
        if self.tape.structural_targets is not None:
            levels = self._row("structural_targets")[:, :5]
        else:
            live = (self.tape.level_from <= now) & (now < self.tape.level_to)
            live &= self.tape.level_resistance & (self.tape.level_lower > ask[:, None])
            levels = (
                torch.where(live, self.tape.level_lower, float("inf"))
                .topk(5, largest=False, sorted=True)
                .values
            )
        levels = torch.where(
            self._row("structural_clock")[:, None] & torch.isfinite(levels),
            levels,
            float("nan"),
        )
        market = torch.stack(
            (
                current,
                close / vwap - 1,
                (ask - bid) / ask,
                self._row("notional"),
                self._row("trades"),
                (now - self.tape.admission).to(torch.float64),
                self._row("macd_line")[:, 0] - self._row("macd_signal")[:, 0],
                self._row("volume"),
            ),
            -1,
        )
        # Reject invalid price observations for every market-derived input;
        # elapsed source age remains a causal duration, not an absolute clock.
        market = torch.cat((market, levels / close[:, None] - 1), -1)
        market = torch.where(observed[:, None], market, float("nan"))
        self.current_atoms.copy_(market)
        history = torch.cat((market[None], self.rule_history), 0).permute(1, 0, 2)
        return evaluate(
            self.clauses, self.connectors, market[None].expand(self.b, -1, -1), history
        )

    def _observe_rule_history(self, close, observed):
        if self.precompute_rules:
            return
        self.rule_history.copy_(
            torch.cat(
                (
                    self.current_atoms[None],
                    self.rule_history[:-1],
                ),
                0,
            )
        )

    @property
    def fingerprint(self):
        payload = dict(
            source=self.tape.provenance,
            settings=asdict(self.settings),
            grammar=self.space.manifest(),
            genomes=self.genomes.tolist(),
            ledger_mode=self.ledger_mode,
            maximum_fills=self.maximum_fills,
        )
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
