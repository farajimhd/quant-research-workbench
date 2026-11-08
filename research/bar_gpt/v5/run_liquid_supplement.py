"""Queue the 500 GB v6 liquid supplement behind successful base completion.

The watcher is a normal restartable workstation job, not a calendar automation.
It never interprets STOPPED/FAILED or a stale terminal screen as completion.
--prepare-only seals metadata/universe without reading any canonical market events.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.cohort_v1 import COHORT_VERSION, members
from research.bar_gpt.v5.shards.phase1_contract import VERSION, digest, write_json


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6-liquid500"),
    )
    parser.add_argument("--target-bytes", type=int, default=500_000_000_000)
    parser.add_argument("--top", type=int, default=512)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--io-workers", type=int, default=16)
    parser.add_argument("--chunk-events", type=int, default=500000)
    parser.add_argument("--coarse-days", type=int, default=20)
    parser.add_argument("--query-memory-gib", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--endpoint")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--panel", action="store_true")
    parser.add_argument(
        "--upgrade-from",
        type=Path,
        help="Preserve a cleanly drained coarse-only supplement into a new sibling component",
    )
    args = parser.parse_args(argv)
    root = Path("D:/TradingML/runtimes").resolve()
    args.base, args.output = args.base.resolve(), args.output.resolve()
    if (
        not root.is_dir()
        or not args.base.is_relative_to(root)
        or not args.output.is_relative_to(root)
        or args.base.parent != args.output.parent
        or args.base == args.output
    ):
        parser.error(
            "Use distinct sibling datasets beneath the available workstation runtime root"
        )
    if (
        not 1 <= args.workers <= 64
        or not 1 <= args.io_workers <= 32
        or args.target_bytes < 10**9
        or args.top < 1
    ):
        parser.error(
            "Require 1..64 workers, 1..32 I/O workers, positive universe and at least 1 GB capacity"
        )
    if (
        args.chunk_events < 1000
        or not 1 <= args.coarse_days <= 100
        or args.query_memory_gib <= 0
    ):
        parser.error(
            "Invalid bounded event chunk, coarse date span or query memory limit"
        )
    return args


def register_cohort(args):
    """Add an explicit sidecar family without mutating any running base contract or files."""
    root = args.base.parent / "phase1-v6-cohort"
    root.mkdir(exist_ok=True)
    body = dict(
        version=COHORT_VERSION,
        schema=VERSION,
        cohort_id="bargpt-2019-2025-phase1-v6",
        target_bytes=1_000_000_000_000 + args.target_bytes,
        members=[
            dict(name="base", path=f"../{args.base.name}"),
            dict(name="liquid500", path=f"../{args.output.name}"),
        ],
    )
    path = root / "cohort.json"
    wrapper = dict(cohort=body, hash=digest(body))
    if path.exists() and json.loads(path.read_text()) != wrapper:
        previous = json.loads(path.read_text())
        upgrade_path = args.output / "upgrade-audit.json"
        if not getattr(args, "upgrade_from", None) or not upgrade_path.exists():
            raise ValueError(
                "A different cohort is registered; require a verified explicit upgrade"
            )
        audit = json.loads(upgrade_path.read_text())
        expected = dict(
            body,
            members=[
                body["members"][0],
                dict(name="liquid500", path=f"../{Path(args.upgrade_from).name}"),
            ],
        )
        if (
            previous != dict(cohort=expected, hash=digest(expected))
            or audit.get("audit_hash")
            != digest({k: v for k, v in audit.items() if k != "audit_hash"})
            or audit.get("state") != "passed"
        ):
            raise ValueError(
                "Cohort replacement is not certified by the requested upgrade"
            )
        write_json(args.output / "previous-cohort.json", previous)
    write_json(path, wrapper)
    members(
        root
    )  # Resolve and validate the actual paths written for laptop/workstation readers.


def dependency_ready(base):
    """Require a successful durable result and a released OS controller lock; return its held handle."""
    path = base / "result.json"
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    if (
        result.get("state")
        not in {"quotas_reached", "budget_reached", "inventory_exhausted"}
        or result.get("failed")
        or result.get("samples", 0) < 1
    ):
        return None
    launch_path = base / "launch.json"
    if (
        launch_path.exists()
        and path.stat().st_mtime < json.loads(launch_path.read_text())["started"]
    ):
        return None  # A prior successful result cannot certify a newer failed or interrupted launch.
    progress_path = base / "progress.json"
    if progress_path.exists():
        progress = json.loads(progress_path.read_text())
        if (
            progress.get("state") != result["state"]
            or progress.get("samples") != result["samples"]
        ):
            return None
    import msvcrt

    owner = (base / "controller.lock").open("r+b")
    try:
        owner.seek(0)
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        owner.close()
        return None
    # Hold the finished base's lock through extraction: its anti-join inventory stays immutable.
    return owner


def main(argv=None):
    args = arguments(argv)
    from research.bar_gpt.v5.supplement_v1 import prepare, run

    queue = args.output / "queue.json"
    try:
        wrapper, _ = prepare(args)
        from research.bar_gpt.v5.supplement_upgrade_v1 import upgrade

        upgrade(args, wrapper)
        register_cohort(args)
    except Exception as error:
        if args.output.is_dir():
            write_json(
                queue,
                dict(
                    state="failed",
                    updated=time.time(),
                    error=f"{type(error).__name__}: {error}",
                ),
            )
        raise
    if args.prepare_only:
        write_json(
            queue,
            dict(
                state="planned",
                updated=time.time(),
                base=str(args.base),
                target_bytes=args.target_bytes,
                source_revision=args.source_revision,
            ),
        )
        return 0
    import msvcrt

    queue_owner = (args.output / "queue.lock").open("a+b")
    queue_owner.write(b"0")
    queue_owner.flush()
    queue_owner.seek(0)
    msvcrt.locking(queue_owner.fileno(), msvcrt.LK_NBLCK, 1)
    owner = None
    observer = None
    try:
        while owner is None:
            if (args.output / "STOP").exists():
                write_json(queue, dict(state="stopped", updated=time.time()))
                return 0
            owner = dependency_ready(args.base)
            write_json(
                queue,
                dict(
                    state="ready" if owner else "waiting_base",
                    updated=time.time(),
                    base=str(args.base),
                    target_bytes=args.target_bytes,
                    source_revision=args.source_revision,
                ),
            )
            if owner is None:
                time.sleep(15)
        if args.panel:
            observer = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-u",
                    "-m",
                    "research.bar_gpt.v5.shards.progress",
                    "--output",
                    str(args.output),
                ],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            write_json(
                args.output / "observer.json",
                dict(pid=observer.pid, started=time.time()),
            )
        write_json(
            queue,
            dict(
                state="running",
                updated=time.time(),
                source_revision=args.source_revision,
            ),
        )
        code = run(args)
        result = json.loads((args.output / "result.json").read_text())
        write_json(
            queue,
            dict(
                state=result["state"] if code == 0 else "failed",
                updated=time.time(),
                exit_code=code,
            ),
        )
        return code
    except (Exception, KeyboardInterrupt) as error:
        failure = dict(
            state="failed",
            updated=time.time(),
            error=f"{type(error).__name__}: {error}",
        )
        write_json(queue, failure)
        status_path = args.output / "progress.json"
        snapshot = json.loads(status_path.read_text()) if status_path.exists() else {}
        write_json(status_path, dict(snapshot, **failure))
        raise
    finally:
        if observer is not None and observer.poll() is None:
            observer.terminate()  # Close only this job's observer after its controller has finished or failed.
            observer.wait(timeout=10)
        if owner is not None:
            owner.close()
        queue_owner.close()


if __name__ == "__main__":
    print(
        "Equivalent: "
        + subprocess.list2cmdline(
            [sys.executable, "-B", "-u", str(Path(__file__).resolve()), *sys.argv[1:]]
        ),
        flush=True,
    )
    raise SystemExit(main())
