"""Synthetic correctness/performance qualification; never a grid experiment."""
import os
import sys
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import argparse
from time import perf_counter
from uuid import uuid4
import torch

from .fixtures import synthetic_tape
from .grid import Candidate
from .runner import SqueezeRunner
from .runtime import DEFAULT, code_hash, configure_caches, require_runtime, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--listings", type=int, default=16)
    parser.add_argument("--batch", type=int, default=8)
    args = parser.parse_args(argv)
    if not (30 <= args.seconds <= 3600 and 1 <= args.listings <= 256 and 1 <= args.batch <= 32):
        parser.error("Synthetic qualification exceeds bounded defaults")
    configure_caches()
    torch.set_num_threads(1)
    configs = [Candidate("macd", macd_mask=8, positions=(5, 10, 15)[i % 3],
               trailing=("adaptive", "step")[i % 2],
               target=("percentage", "structural")[(i // 2) % 2]) for i in range(args.batch)]
    tape = synthetic_tape(seconds=args.seconds, listings=args.listings)
    cpu = SqueezeRunner(tape, configs)
    reference = cpu.run()
    report = {"synthetic_only": True, "code": code_hash(), "seconds": args.seconds,
              "listings": args.listings, "batch": args.batch,
              "cpu_eager_seconds": reference["replay_seconds"], "backends": {}}
    device_tape = tape.to(args.device)
    for backend in (("eager", "cudagraph", "compiled_graph") if args.device == "cuda" else ("compile",)):
        runner = SqueezeRunner(device_tape, configs, backend=backend).compile()
        timings = []
        for _ in range(3):
            observed = runner.run()
            timings.append(observed["replay_seconds"])
        for key in ("cash", "equity", "realized", "fees", "drawdown", "entered", "rotations", "fill_count"):
            if not torch.allclose(reference[key], observed[key].cpu(), atol=1e-7, rtol=0):
                raise AssertionError("CPU/device accounting mismatch: " + key)
        if not torch.allclose(cpu.ledger, runner.ledger.cpu(), atol=1e-7, rtol=0):
            raise AssertionError("CPU/device fill ledger mismatch")
        report["backends"][backend] = {"setup_seconds": runner.setup_seconds,
            "replay_seconds": timings, "median_seconds": sorted(timings)[1], "full_ledger_parity": True}
        # Change candidate values without recompilation, then replay/reset to
        # qualify the in-place grid batching boundary as well.
        altered = [Candidate("signal", positions=5) for _ in configs]
        runner.set_candidates(altered)
        actual = runner.run()
        expected = SqueezeRunner(tape, altered).run()
        if not torch.allclose(actual["cash"].cpu(), expected["cash"], atol=1e-7, rtol=0):
            raise AssertionError("Captured parameter update/reset mismatch")
        del runner
    destination = require_runtime(DEFAULT / "validation" / uuid4().hex)
    write_json(destination / "report.json", report)
    print(str(destination / "report.json"), flush=True)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
