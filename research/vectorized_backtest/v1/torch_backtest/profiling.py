"""Bounded GPU kernel sampling outside the measured objective evaluation."""

import json

import torch


def sample_kernels(runner, folder):
    """Profile one captured block near mid-session, after a causal prefix.

    Full-session traces can contain millions of kernel events. This records
    at most 128 ticks while preserving real portfolio state through the prefix.
    Trace overhead is excluded from replay and pipeline timing.
    """
    if runner.graph is None:
        raise ValueError("Kernel sampling requires a captured CUDA backend")
    blocks = (runner.slots // 2) // runner.graph_steps
    with torch.inference_mode():
        runner.reset()
        for _ in range(blocks):
            runner.graph.replay()
        torch.cuda.synchronize(runner.tape.device)
        with torch.profiler.profile(
            activities=[
                torch.profiler.ProfilerActivity.CPU,
                torch.profiler.ProfilerActivity.CUDA,
            ]
        ) as profile:
            runner.graph.replay()
            torch.cuda.synchronize(runner.tape.device)
    grouped = {}
    for event in profile.events():
        if event.device_type.name != "CUDA":
            continue
        group = grouped.setdefault(
            event.name, {"name": event.name, "count": 0, "microseconds": 0.0}
        )
        group["count"] += 1
        group["microseconds"] += event.device_time_total
    rows = sorted(grouped.values(), key=lambda row: row["microseconds"], reverse=True)
    result = {
        "sample_start_slot": blocks * runner.graph_steps,
        "sample_slots": runner.graph_steps,
        "kernel_count": sum(row["count"] for row in rows),
        "kernel_microseconds": sum(row["microseconds"] for row in rows),
        "kernels": rows,
        "gpu_peak_allocated_gib": torch.cuda.max_memory_allocated(runner.tape.device)
        / 1024**3,
    }
    if not rows:
        raise RuntimeError("CUDA profiler returned no kernel events")
    profile.export_chrome_trace(str(folder / "gpu-trace.json"))
    (folder / "gpu-kernels.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return {key: value for key, value in result.items() if key != "kernels"}
