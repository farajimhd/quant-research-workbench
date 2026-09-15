"""Offline Student-t fit comparison against the hash-verified source."""
import sys
sys.dont_write_bytecode = True
import argparse
import json
from pathlib import Path
import random
import runpy
import subprocess


def main():
    parser = argparse.ArgumentParser(description="Offline Student-t source comparison; no services.")
    parser.add_argument("--rust-executable", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    executable = args.rust_executable.resolve(strict=True)
    if executable.is_relative_to(root):
        parser.error("Build output must be outside ARTE source")
    helpers = runpy.run_path(str(Path(__file__).with_name("check_extraction_parity.py")))
    oracle = helpers["load_reference"](root, "reaction_center.py.txt")
    inputs = [dict(prices=prices, tick=.01) for prices in (
        [], [10.], [10., 11.], [10., 10., 10.], [9.8, 9.9, 10., 10.1, 10.2],
        [10., 10., 10., 10.01, 100.], [1., 1., 1., 100., 100., 100.])]
    for seed in range(80):
        rng = random.Random(seed)
        count = rng.randrange(3, 160)
        tick = rng.choice([.0001, .01, .05])
        width = rng.choice([.01, .1, 1., 5.])
        prices = [max(.01, 20.+rng.gauss(0., width)) for _ in range(count)]
        if seed % 4 == 0:
            prices = [round(p, 2) for p in prices]
        inputs.append(dict(prices=prices, tick=tick))
    run = subprocess.run([str(executable)], input=json.dumps(inputs), text=True,
                         capture_output=True, check=True, timeout=120)
    outputs = json.loads(run.stdout)
    if len(outputs) != len(inputs):
        raise AssertionError("Fit result count differs")
    failures = []
    maximum_error = 0.
    for index, (case, output) in enumerate(zip(inputs, outputs)):
        expected = oracle.fit(case["prices"], case["tick"])
        actual = output.get("Ok")
        if actual is None or actual["status"] != expected["status"]:
            failures.append(f"case {index}: status {expected['status']} != {output}")
            continue
        if expected["status"] != "estimated":
            continue
        for key in ("center", "scale"):
            delta = abs(actual[key]-expected[key])
            maximum_error = max(maximum_error, delta / case["tick"])
            # Tick-coordinate error is the meaningful geometry unit. The solver
            # is independently versioned; this tolerance is not exact parity.
            if delta > max(case["tick"] * 1e-3, abs(expected[key]) * 1e-8):
                failures.append(f"case {index}: {key} differs by {delta/case['tick']:.6g} ticks")
        if actual["scale_at_floor"] != expected["scale_at_floor"]:
            failures.append(f"case {index}: scale-floor classification differs")
    if failures:
        print(f"FAIL: {len(failures)} numerical/status mismatches", file=sys.stderr)
        for failure in failures[:10]:
            print(failure, file=sys.stderr)
        return 1
    print(f"PASS: {len(inputs)} fit cases; maximum difference {maximum_error:.6g} ticks")
    print("Tolerance: max(0.001 ticks, 1e-8 of expected value); statuses must match")
    print("Not certified: all-session fit convergence or downstream trading decisions")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
