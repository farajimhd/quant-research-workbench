"""Compile fixed atomic policies over completed source history before replay.

Atomic inputs here depend ONLY on immutable market tape, never account state.
Chunking bounds intermediates; [T,B,N] boolean gates replace four dispatches
and a history copy on every causal portfolio tick. No future slot is selected.
"""

from time import perf_counter

import torch

from .rules import HISTORY, evaluate


def atomic_chunk(tape, indices):
    """Same atomic contract as SearchRunner, for a completed time-index vector."""
    close = tape.close.index_select(0, indices)
    observed = tape.observed.index_select(0, indices)
    bid, ask = tape.bid.index_select(0, indices), tape.ask.index_select(0, indices)
    if tape.structural_targets is None:
        raise ValueError(
            "Precomputed rules require the completed streaming V7 target layout"
        )
    levels = tape.structural_targets.index_select(0, indices)[..., :5]
    valid = tape.structural_clock.index_select(0, indices)[..., None] & torch.isfinite(
        levels
    )
    levels = torch.where(valid, levels, float("nan"))
    values = torch.stack(
        (
            close,
            close / tape.vwap.index_select(0, indices) - 1,
            (ask - bid) / ask,
            tape.notional.index_select(0, indices),
            tape.trades.index_select(0, indices),
            (tape.clocks.index_select(0, indices)[:, None] - tape.admission).to(
                torch.float64
            ),
            tape.macd_line.index_select(0, indices)[..., 0]
            - tape.macd_signal.index_select(0, indices)[..., 0],
            tape.volume.index_select(0, indices),
        ),
        -1,
    )
    return torch.where(
        observed[..., None],
        torch.cat((values, levels / close[..., None] - 1), -1),
        float("nan"),
    )


def chunk_gate(clauses, connectors, atoms, before, size, listings):
    # atoms includes a 12-slot prefix. Invalid pre-session history is NaN.
    offsets = (
        torch.arange(size, device=atoms.device)[:, None]
        + before
        - torch.arange(HISTORY + 1, device=atoms.device)[None]
    )
    history = (
        atoms[offsets]
        .permute(0, 2, 1, 3)
        .reshape(size * listings, HISTORY + 1, atoms.shape[-1])
    )
    current = history[:, 0]
    return (
        evaluate(
            clauses, connectors, current[None].expand(len(clauses), -1, -1), history
        )
        .reshape(len(clauses), size, listings)
        .permute(1, 0, 2)
    )


class RuleCompiler:
    def __init__(self, runner, *, chunk_seconds=128):
        self.runner, self.chunk_seconds = runner, chunk_seconds
        self.compiled = (
            torch.compile(chunk_gate, fullgraph=True)
            if runner.backend in ("compile", "compiled_graph")
            else chunk_gate
        )
        t, n = runner.tape.close.shape
        required = t * runner.b * n
        if runner.tape.device.type == "cuda":
            free, _ = torch.cuda.mem_get_info(runner.tape.device)
            scratch = runner.b * chunk_seconds * n * (HISTORY + 1) * 8 * 3
            if required + scratch > free * 0.7:
                raise MemoryError(
                    "Rule gate and chunk workspace exceed GPU headroom; lower population explicitly"
                )
        runner.rule_gate = torch.zeros(
            (t, runner.b, n), dtype=torch.bool, device=runner.tape.device
        )

    def prepare(self, progress=None):
        r = self.runner
        t, n = r.tape.close.shape
        started = perf_counter()
        updated = started
        with torch.inference_mode():
            for lo in range(0, t, self.chunk_seconds):
                hi = min(t, lo + self.chunk_seconds)
                indices = torch.arange(max(0, lo - HISTORY), hi, device=r.tape.device)
                atoms = atomic_chunk(r.tape, indices)
                missing = max(0, HISTORY - lo)
                if missing:
                    atoms = torch.cat(
                        (
                            torch.full(
                                (missing, n, atoms.shape[-1]),
                                float("nan"),
                                dtype=atoms.dtype,
                                device=atoms.device,
                            ),
                            atoms,
                        ),
                        0,
                    )
                result = self.compiled(
                    r.clauses, r.connectors, atoms, HISTORY, hi - lo, n
                )
                r.rule_gate[lo:hi].copy_(result)
                if progress and perf_counter() - updated >= 1:
                    if r.tape.device.type == "cuda":
                        torch.cuda.synchronize(r.tape.device)
                    progress(
                        dict(
                            stage="Compile fixed policy gates",
                            completed_seconds=hi,
                            total_seconds=t,
                        )
                    )
                    updated = perf_counter()
        if r.tape.device.type == "cuda":
            torch.cuda.synchronize(r.tape.device)
        return perf_counter() - started
