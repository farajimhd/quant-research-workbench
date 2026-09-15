"""Offline forming-MACD comparison; no app imports, network, services or output files."""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-executable", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    executable = args.rust_executable.resolve(strict=True)
    if executable.is_relative_to(root):
        parser.error("Build output must be outside ARTE source")
    destination = "tests/reference/src/trading_runtime/historical_hod.py.txt"
    manifest = json.loads((root / "tests/reference/origin.json").read_text())
    entry = next(item for item in manifest["files"] if item["destination"] == destination)
    source = (root / destination).read_bytes()
    if hashlib.sha256(source).hexdigest() != entry["sha256"]:
        raise ValueError("Frozen strategy SHA-256 mismatch")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "forming_macd"]
    if len(functions) != 1:
        raise ValueError("Expected exactly one frozen forming_macd function")
    namespace = {"isfinite": math.isfinite}
    # Compile the exact function AST only. No imports or parent-app helpers run.
    exec(compile(ast.Module(body=functions, type_ignores=[]), destination, "exec"), namespace)
    oracle = namespace["forming_macd"]
    cases = []
    for seed in range(32):
        rng = random.Random(seed)
        observations = []
        fast = slow = signal = None
        for second in range(100, 220):
            close = max(.01, 10. + rng.gauss(0., 3.))
            if second % 5 == 0 and rng.random() > .2:
                fast = close if fast is None else fast + 2/13 * (close-fast)
                slow = close if slow is None else slow + 2/27 * (close-slow)
                line = fast-slow
                signal = line if signal is None else signal + .2 * (line-signal)
                unknown = rng.random() < .1
                observations.append(dict(timeframe=5, at_ns=second*10**9, open=close, close=close,
                                         line=None if unknown else line, signal=None if unknown else signal))
            if rng.random() > .1:
                observations.append(dict(timeframe=1, at_ns=second*10**9, open=close, close=close,
                                         line=None, signal=None))
        cases.append(observations)
    output = json.loads(subprocess.run([str(executable)], input=json.dumps(cases), text=True,
                                      capture_output=True, check=True, timeout=60).stdout)
    if len(output) != len(cases):
        raise AssertionError("MACD case count differs")
    count = 0
    maximum_error = 0.
    for case, results in zip(cases, output):
        if len(case) != len(results):
            raise AssertionError("MACD observation count differs")
        state = {}
        for sample, result in zip(case, results):
            now = sample["at_ns"] // 10**9
            observation = SimpleNamespace(observed_at=datetime.fromtimestamp(now, timezone.utc),
                                          source_timeframe=f"{sample['timeframe']}s", price=sample["close"],
                                          macd_line=sample["line"], macd_signal=sample["signal"])
            expected = oracle(observation, state)
            if sample["timeframe"] == 5:
                expected = dict(state["completed_macd"], kind="completed", base_at=now)
            actual = result.get("Ok")
            if actual is None:
                raise AssertionError(f"Rust rejected valid MACD fixture: {result}")
            kind = {"completed": "Completed", "forming": "Forming", "forming_unavailable": "Unavailable"}[expected["kind"]]
            if actual["kind"] != kind:
                raise AssertionError("MACD availability/kind differs")
            base = expected.get("base_at")
            if actual["base_at_ns"] != (None if base is None else int(base*10**9)):
                raise AssertionError("MACD base clock differs")
            for key in ("line", "signal"):
                if expected[key] is None:
                    if actual[key] is not None:
                        raise AssertionError("Unavailable MACD field was fabricated")
                else:
                    if actual[key] is None:
                        raise AssertionError("Available MACD field was lost")
                    error = abs(actual[key]-expected[key])
                    maximum_error = max(maximum_error, error)
                    if not math.isclose(actual[key], expected[key], rel_tol=1e-12, abs_tol=1e-12):
                        raise AssertionError(f"MACD {key} numerical mismatch: {error}")
            expected_positive = expected["line"] is not None and expected["signal"] is not None and expected["line"] > expected["signal"]
            actual_positive = actual["line"] is not None and actual["signal"] is not None and actual["line"] > actual["signal"]
            if actual_positive != expected_positive:
                raise AssertionError("MACD bullish classification differs")
            count += 1
    print(f"PASS: {len(cases)} histories, {count} MACD observations; maximum absolute difference {maximum_error:.6g}")
    print("Not certified: full episode/strategy parity, historical warmup sufficiency or trading decisions")


if __name__ == "__main__":
    main()
