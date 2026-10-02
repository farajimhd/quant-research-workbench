"""Bounded kernel attribution outside all reported replay timings."""

import json
from functools import wraps

import torch


def profile_stages(replay, path):
    """Attribute GPU launches to nonoverlapping CPU orchestration stages.

    CUDA execution is asynchronous: CPU range duration is not GPU runtime.
    Correlation IDs link each actual GPU kernel to its CPU launch, then to the
    enclosing stage. This counts device kernels once, including idle-market
    work, without summing overlapping profiler parent/child aggregates.
    """
    names = (
        "_observe_inputs",
        "reprice_step",
        "_broker_inputs",
        "_match_account",
        "_record",
        "_manage",
        "amend_step",
        "_decisions",
        "_prepare_funding",
        "_admit",
    )
    originals = {name: getattr(replay, name) for name in names}

    def wrapped(name, function):
        @wraps(function)
        def call(*args, **kwargs):
            with torch.profiler.record_function("stage:" + name):
                return function(*args, **kwargs)

        return call

    replay.reset()
    try:
        for name, function in originals.items():
            setattr(replay, name, wrapped(name, function))
        with (
            torch.inference_mode(),
            torch.profiler.profile(
                activities=[
                    torch.profiler.ProfilerActivity.CPU,
                    torch.profiler.ProfilerActivity.CUDA,
                ]
            ) as profile,
        ):
            replay.tick()
            torch.cuda.synchronize()
        profile.export_chrome_trace(str(path))
    finally:
        for name, function in originals.items():
            setattr(replay, name, function)
    events = json.loads(path.read_text(encoding="utf-8"))["traceEvents"]
    return summarize_trace(events, names)


def summarize_trace(events, names):
    """Pure attribution, also usable to audit retained profiler traces."""
    ranges = [
        event
        for event in events
        if event.get("ph") == "X" and event["name"].startswith("stage:")
    ]
    launches = {
        event.get("args", {}).get("correlation"): event
        for event in events
        if event.get("cat") in {"cuda_runtime", "cuda_driver"}
        and "Launch" in event["name"]
    }
    totals = {
        name: {"kernel_count": 0, "device_us": 0.0} for name in (*names, "unattributed")
    }
    kernels = [event for event in events if event.get("cat") == "kernel"]
    for kernel in kernels:
        launch = launches.get(kernel.get("args", {}).get("correlation"))
        stage = "unattributed"
        if launch is not None:
            for span in ranges:
                if span["ts"] <= launch["ts"] <= span["ts"] + span["dur"]:
                    stage = span["name"].removeprefix("stage:")
                    break
        totals[stage]["kernel_count"] += 1
        totals[stage]["device_us"] += kernel["dur"]
    return {
        "strategy_ticks": 1,
        "kernel_count": len(kernels),
        "profiled_device_microseconds": sum(event["dur"] for event in kernels),
        "stages": totals,
        "excluded_from_replay_timing": True,
    }
