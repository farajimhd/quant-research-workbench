#!/usr/bin/env python3
"""Run complete canonical days locally, then compare original/optimized artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(r"D:\TradingML\runtimes")
FIELDS = ("ticker", "start", "end", "algorithm_version", "events", "input_sha256",
          "emissions_sha256", "boundaries", "days", "status", "scope")


def compare(original: dict, candidate: dict) -> dict:
    differences = [field for field in FIELDS if field not in original or field not in candidate or original[field] != candidate[field]]
    if original.get("status") != "completed" or candidate.get("status") != "completed":
        differences.append("incomplete_run")
    if differences:
        raise RuntimeError(f"Parity failed for {original.get('ticker')}: {', '.join(differences)}")
    return {"ticker": original["ticker"], "events": original["events"],
            "days": len(original["days"]), "exact_parity": True,
            "baseline_apply_seconds": original["apply_seconds"],
            "candidate_apply_seconds": candidate["apply_seconds"],
            "apply_speedup": original["apply_seconds"] / candidate["apply_seconds"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--tickers", nargs="+", default=["JUNS", "SUGP", "SDOT"])
    parser.add_argument("--start", default="2026-08-19")
    parser.add_argument("--end", default="2026-08-21")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise RuntimeError("Output must be under the laptop runtime root")
    output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve(strict=True)
    with binary.open("rb") as binary_file:
        binary_hash = hashlib.file_digest(binary_file, "sha256").hexdigest()
    manifest = {"binary_sha256": binary_hash,
                "start": args.start, "end": args.end, "tickers": args.tickers, "status": "running", "completed": [], "comparisons": []}
    manifest_path = output / "validation.json"
    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    save()
    try:
        for ticker in args.tickers:
            print(f"Active {ticker}; completed {len(manifest['completed'])}/{len(args.tickers)}; queued {len(args.tickers)-len(manifest['completed'])-1}", flush=True)
            with (output / f"{ticker}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(binary), ticker, args.start, args.end, str(output)], stdout=log, stderr=log, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                started = time.monotonic()
                try:
                    while process.poll() is None:
                        if time.monotonic()-started > 960:
                            raise TimeoutError(f"{ticker} exceeded launcher deadline")
                        time.sleep(1)
                    if process.returncode:
                        raise RuntimeError(f"{ticker} failed ({process.returncode}); inspect {output / (ticker+'.log')}")
                finally:
                    if process.poll() is None:
                        (output / "STOP").touch()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
            result = json.loads((output / f"{ticker}.json").read_text())
            if result.get("status") != "completed":
                raise RuntimeError(f"{ticker} incomplete")
            manifest["completed"].append(ticker)
            if args.baseline:
                baseline = json.loads((args.baseline / f"{ticker}.json").read_text())
                measured = compare(baseline, result)
                manifest["comparisons"].append(measured)
                print(f"{ticker}: exact parity; {result['events']:,} events; apply speedup {measured['apply_speedup']:.2f}x", flush=True)
            else:
                print(f"{ticker}: baseline complete; {result['events']:,} events; apply {result['apply_seconds']:.2f}s", flush=True)
            save()
        manifest["status"] = "completed"
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
