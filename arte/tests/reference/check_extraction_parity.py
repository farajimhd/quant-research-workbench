"""Pure offline source comparison. Does not import or start the parent application."""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
import random
import subprocess
import types


def verify_dependencies(root):
    verified = {}
    requirements = root / "tests/reference/requirements.txt"
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("==")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Offline oracle dependencies must have exact version pins")
        name, expected = parts
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError as error:
            raise ValueError(f"Offline oracle dependency missing: {name}=={expected}") from error
        if actual != expected:
            raise ValueError(f"Offline oracle dependency mismatch: {name} expected {expected}, found {actual}")
        verified[name] = actual
    if set(verified) != {"numpy", "scipy"}:
        raise ValueError("Offline oracle requires explicit NumPy and SciPy pins")
    print("Oracle dependencies: " + ", ".join(f"{name}=={version}" for name, version in sorted(verified.items())))


def load_reference(root, filename="historical_session_levels.py.txt"):
    verify_dependencies(root)
    manifest = json.loads((root / "tests/reference/origin.json").read_text())
    destination = "tests/reference/src/market_engine/" + filename
    entry = next(row for row in manifest["files"] if row["destination"] == destination)
    path = (root / destination).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Reference path leaves ARTE")
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != entry["sha256"]:
        raise ValueError("Frozen extractor SHA-256 mismatch")
    # This module uses only stdlib, NumPy and SciPy. Relative parent-app imports
    # are not resolved. Other reference snapshots are not executed by this test.
    name = "arte_frozen_oracle_" + filename.replace(".", "_")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def cases(reference):
    settings = reference.asdict(reference.Settings())
    result = []
    for seed in range(64):
        rng = random.Random(seed)
        bars = []
        price = 10.0
        second = 1_700_000_000
        for i in range(240):
            second += 90 if seed % 4 == 0 and i % 45 == 0 else rng.choice([1, 1, 1, 2])
            open_price = price
            price = max(1., round(price + rng.choice([-0.2, -0.1, 0., 0.1, 0.2]), 2))
            bars.append(dict(t=second, open=open_price, close=price,
                             high=round(max(open_price, price) + rng.choice([0., 0.01, 0.02]), 2),
                             low=round(min(open_price, price) - rng.choice([0., 0.01, 0.02]), 2),
                             volume=float(rng.randrange(0, 10000))))
        profile = [dict(price=b["close"], volume=b["volume"]) for b in bars] if seed % 2 else []
        result.append(dict(bars=bars, profile=profile, ticker="PARITY", session="2026-09-14",
                           available_at=second, settings=dict(settings)))
    # Repeated role evidence, flat peaks, and gaps have deterministic edge cases.
    for prices in ([10., 11., 10., 11., 10., 11., 10.], [10.] * 8,
                   [10., 10.5, 10.5, 10.5, 10., 10.5, 10.], [10., 100., 10.]):
        bars = [dict(t=1_700_000_000+i, open=p, high=p+0.01, low=p-0.01, close=p, volume=100.)
                for i, p in enumerate(prices)]
        result.append(dict(bars=bars, profile=[], ticker="EDGES", session="2026-09-14",
                           available_at=bars[-1]["t"], settings=dict(settings)))
    invalid = dict(result[0], available_at=result[0]["available_at"]-1)
    result.append(invalid)
    limited = dict(result[0], settings=dict(settings, maximum_candidates=1))
    result.append(limited)
    return result


def compare(expected, actual, path="result"):
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            raise AssertionError(f"{path}: keys differ")
        for key in expected:
            compare(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            raise AssertionError(f"{path}: counts {len(expected)} != {len(actual)}")
        for index, (a, b) in enumerate(zip(expected, actual)):
            compare(a, b, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not isinstance(actual, (int, float)) or not math.isclose(expected, actual, rel_tol=1e-10, abs_tol=1e-10):
            raise AssertionError(f"{path}: {expected!r} != {actual!r}")
    elif expected != actual:
        raise AssertionError(f"{path}: {expected!r} != {actual!r}")


def project_level(row, rust=False):
    keys = ("id", "lower", "upper", "price", "support_rejections", "resistance_rejections",
            "accepted_crossings", "profile_volume", "proposal_sources", "closing_role",
            "evidence_role", "available_at", "role_segments", "first_confirmed_at")
    result = {key: row[key] for key in keys}
    result["encounters"] = [dict(event, reason=event.get("reason")) for event in row["encounters"]]
    result["rejection_reason"] = row.get("rejection_reason" if rust else "reason")
    result["role_quality"] = ({"support": row["support_rejection_fraction"],
                               "resistance": row["resistance_rejection_fraction"]} if rust
                              else row["role_rejection_fraction"])
    for key in ("proposal_count", "proposal_min", "proposal_max"):
        result[key] = row[key] if rust else row["geometry_evidence"][key]
    return result


def main():
    parser = argparse.ArgumentParser(description="Compare ARTE historical extraction against its frozen source; offline only.")
    parser.add_argument("--rust-executable", required=True, type=Path,
                        help="Already-built extraction_parity example, outside the source tree")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    executable = args.rust_executable.resolve(strict=True)
    if executable.is_relative_to(root):
        parser.error("Build output must be outside ARTE source")
    reference = load_reference(root)
    inputs = cases(reference)
    process = subprocess.run([str(executable)], input=json.dumps(inputs), text=True,
                             capture_output=True, timeout=120, check=True)
    actual = json.loads(process.stdout)
    if len(actual) != len(inputs):
        raise AssertionError("Rust bridge returned a different case count")
    selected_levels = 0
    rejected_levels = 0
    failures = []
    for index, (case, output) in enumerate(zip(inputs, actual)):
        try:
            try:
                expected = reference.extract(case["bars"], case["profile"], ticker=case["ticker"],
                    session=case["session"], available_at=case["available_at"], source={},
                    settings=reference.Settings(**case["settings"]))
            except ValueError:
                if "error" not in output:
                    raise AssertionError("Rust accepted source-rejected input")
                continue
            if "ok" not in output:
                raise AssertionError(f"Rust rejected source-accepted input: {output['error']}")
            observed = output["ok"]
            compare(expected["geometry"], observed["geometry"], "geometry")
            compare(expected["counts"]["proposals"], observed["proposal_count"], "proposal_count")
            for group in ("levels", "rejected"):
                compare([project_level(row) for row in expected[group]],
                        [project_level(row, rust=True) for row in observed[group]], group)
                if group == "levels":
                    selected_levels += len(expected[group])
                else:
                    rejected_levels += len(expected[group])
        except (AssertionError, KeyError, TypeError) as error:
            failures.append(f"case {index}: {error}")
    if failures:
        for message in failures[:10]:
            print(message, file=sys.stderr)
        print(f"FAIL: {len(failures)}/{len(inputs)} cases differ", file=sys.stderr)
        return 1
    if selected_levels == 0 or rejected_levels == 0:
        raise AssertionError("Parity cases must exercise both selected and rejected levels")
    print(f"PASS: {len(inputs)} cases; {selected_levels} selected, {rejected_levels} rejected levels")
    print("Compared: geometry, identities, decisions, encounters, roles and volume")
    print("Not certified: fit solver, consolidation, streaming or live trading")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
