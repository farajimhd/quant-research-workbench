"""Prepare shared persisted inputs one session at a time; never run a Backtest.

The JSON plan is an explicit list of session_date, build_id, archive_directory.
Publishers own exact coverage verification and resume. This controller never
uses its status file as data authority: every resumed stage revalidates through
the original publisher. A STOP file drains the current stage before stopping.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[2]
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True


def read_plan(path: Path, runtime: Path) -> list[dict[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plan must contain explicit session rows")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {
                "session_date", "build_id", "archive_directory"}
                or not all(isinstance(v, str) for v in row.values())
                or not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", row["build_id"])):
            raise ValueError("Invalid pinned session plan")
        if date.fromisoformat(row["session_date"]).isoformat() != row["session_date"]:
            raise ValueError("Session must be an ISO date")
        archive = Path(row["archive_directory"]).resolve(strict=True)
        if not archive.is_relative_to(runtime.resolve(strict=True)):
            raise ValueError("Archive is outside runtime authority")
        if not (archive / (row["build_id"] + ".json")).is_file():
            raise ValueError("Original build archive is missing")
    days = [row["session_date"] for row in rows]
    if days != sorted(set(days)):
        raise ValueError("Sessions must be unique and chronological")
    return rows


def stages(row: dict[str, str], workers: int, runtime: Path):
    day, build = row["session_date"], row["build_id"]
    pin = ["--session-date", day, "--build-id", build]
    yield "identity", ["publish_strategy_one_identities.py", *pin,
                       "--apply", "--confirm-identity-publication"]
    yield "candidates", ["publish_strategy_one_candidates.py", *pin,
                         "--read-workers", str(workers), "--write-workers", str(workers),
                         "--apply", "--confirm-candidate-publication"]
    yield "execution-prices", ["build_liquidity_execution_prices.py", "--date", day,
                               "--build-id", build, "--runtime", str(runtime),
                               "--archive-directory", row["archive_directory"],
                               "--workers", str(workers), "--apply",
                               "--confirm-eligible-price-publication"]
    for name, script, confirmation in (
        ("pivots", "pivots", "pivot"), ("hod", "hod", "hod"),
        ("v7", "v7_intervals", "v7-interval"),
        ("entry", "entry_evidence", "entry"),
    ):
        yield name, [f"publish_strategy_one_{script}.py", *pin, "--workers", str(workers),
                     "--apply", f"--confirm-{confirmation}-publication"]
    yield "verify", ["verify_strategy_one_inputs.py", *pin]
    yield "app-preflight", ["smoke_strategy_one_app_route.py", "--session", day,
                            "--minutes", "960"]


def save(path: Path, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(plan: list[dict[str, str]], output: Path, runtime: Path, workers: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive handle prevents two controllers admitting the same campaign.
    lock = output / "controller.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    state = {"status": "running", "active": None, "completed": [], "failed": [],
             "queued": [row["session_date"] for row in plan], "workers": workers}
    status_path = output / "status.json"
    try:
        for row in plan:
            day = row["session_date"]
            state["queued"].remove(day)
            for name, args in stages(row, workers, runtime):
                if (output / "STOP").exists():
                    state["status"] = "stopped"
                    state["active"] = None
                    save(status_path, state)
                    return 130
                state["active"] = {"session_date": day, "stage": name}
                save(status_path, state)
                print(f"Session {day} | {name} | completed={len(state['completed'])} "
                      f"failed={len(state['failed'])} queued={len(state['queued'])}", flush=True)
                log_path = output / f"{day}-{name}.log"
                command = [sys.executable, "-u", str(ROOT / "scripts" / "clickhouse" / args[0]), *args[1:]]
                began = monotonic()
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(f"\nAttempt {datetime.now(timezone.utc).isoformat()}\n")
                    log.flush()
                    child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                    try:
                        while child.poll() is None:
                            sleep(1)
                    except BaseException:
                        # Do not abandon a producer or leave it writing after controller exit.
                        print("Draining admitted producer before exit...", flush=True)
                        child.wait()
                        raise
                result = {"session_date": day, "stage": name, "exit_code": child.returncode,
                          "seconds": round(monotonic() - began, 3), "log": str(log_path)}
                with (output / "stages.jsonl").open("a", encoding="utf-8") as journal:
                    journal.write(json.dumps(result) + "\n")
                if child.returncode:
                    state["failed"].append(result)
                    print(f"Blocked {day} at {name}: inspect {log_path}", flush=True)
                    break
            else:
                state["completed"].append(day)
            state["active"] = None
            save(status_path, state)
        state["status"] = "failed" if state["failed"] else "complete"
        save(status_path, state)
        return 1 if state["failed"] else 0
    finally:
        lock.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path(r"D:\TradingML\runtimes"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("Use 1..4 workers for this bounded campaign")
    runtime = args.runtime.resolve(strict=True)
    if not args.output.resolve().is_relative_to(runtime):
        parser.error("Output must be inside the runtime root")
    plan = read_plan(args.plan, runtime)
    if not args.apply:
        print(f"Plan only: {len(plan)} sessions, 9 stages each, {args.workers} workers; no writers started")
        return 0
    if platform.node().upper() != "DESKTOP-SAAI85T":
        parser.error("Publication requires the managed workstation")
    return run(plan, args.output.resolve(), runtime, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
