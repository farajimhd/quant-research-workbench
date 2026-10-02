"""Bounded measured batch selection; preserve FP64 account arithmetic.

VRAM capacity is a constraint, not a reason to allocate unused memory. Measure
candidate-seconds/second on the selected tape prefix and retain the fastest
batch that fits. No concurrent accounts share cash or simulated liquidity.
"""
from dataclasses import fields, replace
import gc
from time import perf_counter
import torch

from .runner import SqueezeRunner
from .tape import SqueezeTape


def memory_plan(free_bytes, total_bytes, tape_bytes, listings, maximum_fills=16384):
    if min(free_bytes, total_bytes, listings, maximum_fills) <= 0:
        raise ValueError("Invalid resource snapshot")
    # Preserve at least 10 GiB/15% of device capacity for other processes,
    # compiler workspaces and driver memory. Query again before each allocation.
    reserve = max(10 * 1024**3, int(total_bytes * .15))
    budget = min(int(free_bytes * .75), free_bytes - reserve)
    per = listings * 15 * 260 + maximum_fills * 9 * 8
    # State estimate includes persistent buffers; 6x covers tick intermediates,
    # CUDA graph capture and compilation headroom. Allocation guards stay active.
    choices = [b for b in (32, 64, 128, 256, 512, 1024) if per * b * 6 < budget]
    if not choices:
        raise MemoryError("No batch fits declared GPU headroom; no CPU fallback")
    return {"choices": choices, "state_gib": budget / 1024**3,
            "free_gib": free_bytes / 1024**3, "total_gib": total_bytes / 1024**3,
            "tape_gib": tape_bytes / 1024**3, "reserve_gib": reserve / 1024**3}


def calibrate(tape, grid, settings, *, maximum_fills=16384, graph_steps=16, progress=None, batches=None):
    """Prefix timing only; never publish calibration P&L or reuse its account state."""
    if tape.device.type != "cuda":
        raise ValueError("Workstation calibration requires CUDA")
    free, total = torch.cuda.mem_get_info(tape.device)
    plan = memory_plan(free, total, tape.bytes, len(tape.tickers), maximum_fills)
    if batches is not None:
        if not batches or any(not 1 <= b <= max(plan["choices"]) for b in batches):
            raise MemoryError("Explicit batch exceeds measured resource headroom")
        plan["choices"] = batches
    length = min(128, len(tape.clocks))
    values = {f.name: getattr(tape, f.name) for f in fields(tape)}
    for name in ("clocks", "close", "observed", "high", "low", "vwap", "bid", "ask", "quote_valid",
                 "volume", "notional", "trades", "fill_price", "macd_line", "macd_signal", "structural_clock"):
        values[name] = values[name][:length]
    # Use prefix market geometry. Activate all tickers in timing only so an idle
    # morning prefix still exercises B,N,15 order masks. It is not historical fitness.
    values["admission"] = torch.full_like(tape.admission, int(tape.clocks[0]))
    values["provenance"] = {"synthetic": True, "fingerprint": "gpu-calibration-only",
                            "start_second": int(tape.clocks[0]), "end_second": int(tape.clocks[length-1])}
    witness = SqueezeTape(**values).validate()
    measurements = []
    for b in plan["choices"]:
        if progress:
            progress({"stage": "GPU calibration", "message": f"Measuring batch {b}; compile then 3 prefix replays"})
        # Spread semantic choices throughout the grid, rather than identical leading entries.
        selected = [grid[(i * len(grid) // b) % len(grid)] for i in range(b)]
        runner = SqueezeRunner(witness, selected, settings, backend="compiled_graph",
            graph_steps=graph_steps, maximum_fills=maximum_fills, maximum_state_gib=plan["state_gib"]).compile()
        times = [runner.run()["replay_seconds"] for _ in range(3)]
        seconds = sorted(times)[1]
        measurements.append({"batch": b, "replay_seconds": seconds, "setup_seconds": runner.setup_seconds,
                             "candidate_seconds_per_second": b * length / seconds})
        del runner
        gc.collect()
        torch.cuda.empty_cache()
    best = max(measurements, key=lambda r: r["candidate_seconds_per_second"])
    return {**plan, "batch": best["batch"], "measurements": measurements,
            "device": torch.cuda.get_device_name(tape.device), "prefix_seconds": length,
            "calibration_only": True}
