"""Single-owner overnight quote migration controller, with live panel evidence.

The current trainer is never signalled here. The original phase-two producer
must remain stopped. Each repair child has an independent receipt/STOP ledger.
After both phase-one members finish, publish their repaired cohort and request
the explicit audited checkpoint handoff; do not bypass trainer compatibility.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .shards.contract import digest, write_json
from .quote_repair_v1 import require_complete
from .phase2_shards.telemetry import Telemetry

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

DATASETS = (
    ("phase1-base", "shards-2019-2025-v6"),
    ("phase1-liquid500", "shards-2019-2025-v6-liquid500-v2"),
    ("phase2", "phase2-streams-v1"),
)
SUFFIX = "-quote-v2"


class RepairTelemetry(Telemetry):
    """Reuse the asynchronous online writer; upload only bounded repair counters."""

    def _upload(self):
        with self.lock:
            snapshot, self.pending = self.pending, None
        if snapshot is None:
            return
        metrics = {"repair/state": snapshot["state"]}
        for item in snapshot.get("datasets", []):
            prefix = (
                "repair/"
                + snapshot.get("policy_suffix", "-quote-v1").lstrip("-")
                + "/"
                + item["name"]
                + "/"
            )
            for name in ("completed", "total", "active"):
                metrics[prefix + name] = item.get(name, 0)
            for name, value in item.get("counts", {}).items():
                metrics[prefix + name] = value
        self.journal.write(json.dumps(dict(time=time.time(), **metrics)) + "\n")
        if self.run:
            self.run.log(metrics)
        self.error = None


def status(path):
    """Read bounded live evidence; a missing status is explicitly starting."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(state="starting", updated=0)


def cohort(root, suffix=SUFFIX):
    """Seal sibling repaired members without changing the old cohort manifest."""
    for _, name in DATASETS[:2]:
        require_complete(root / (name + suffix))
    old = status(root / "phase1-v6-cohort/cohort.json")
    body = dict(old["cohort"])
    body["cohort_id"] += suffix
    body["members"] = [
        dict(name=item["name"], path="../" + DATASETS[i][1] + suffix)
        for i, item in enumerate(body["members"])
    ]
    wrapper = dict(cohort=body, hash=digest(body))
    path = root / ("phase1-v6-cohort" + suffix) / "cohort.json"
    path.parent.mkdir(exist_ok=True)
    write_json(path, wrapper)
    # An explicit manifest in the new base lets existing readers discover the
    # new family; relative member paths resolve identically from either parent.
    write_json(root / (DATASETS[0][1] + suffix) / "cohort.json", wrapper)
    return path


def main():
    """Sequence resumable jobs; refresh health evidence every five seconds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt")
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--source-first", action="store_true")
    args = parser.parse_args()
    suffix = "-quote-v4" if args.source_first else SUFFIX
    setup = args.root / (
        "quote-source-first-setup" if args.source_first else "quote-repair-setup"
    )
    setup.mkdir(exist_ok=True)
    import msvcrt

    owner = (setup / "campaign.lock").open("a+b")
    owner.write(b"0")
    owner.flush()
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    if args.source_first:
        import ctypes

        awake = ctypes.windll.kernel32.SetThreadExecutionState
        awake.argtypes = [ctypes.c_uint]
        awake.restype = ctypes.c_uint
        if not awake(0x80000001):
            raise OSError("Cannot hold workstation awake for source-first repair")
    environment = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        POLARS_MAX_THREADS="2",
    )
    snapshot = dict(
        state="running",
        pid=os.getpid(),
        started=time.time(),
        revision=args.source_revision,
        policy_suffix=suffix,
        datasets=[],
    )
    telemetry = RepairTelemetry(
        setup,
        dict(
            job="quote-repair-v1",
            root=str(args.root),
            workers=args.workers,
            source_revision=args.source_revision,
            data_quality=suffix,
            payload_io_priority="thread-background; queries and shared locks remain foreground",
        ),
        project="bargpt-quote-repair-v1",
    )
    snapshot["wandb_url"] = telemetry.url
    for label, name in DATASETS:
        snapshot["datasets"].append(
            dict(name=label, root=name + suffix, state="queued")
        )
    write_json(setup / "execution.json", snapshot)
    for index, (label, name) in enumerate(DATASETS):
        if index == 2:
            sealed = cohort(args.root, suffix)
            handoff = setup / "training-handoff-needed.json"
            if not handoff.exists():
                write_json(
                    handoff,
                    dict(
                        state="audited_handoff_required",
                        cohort=str(sealed),
                        campaign="overnight-v6-20261108",
                        checkpoint_boundary_only=True,
                        wandb_id="1rpdtas2",
                        preserve_optimizer_scheduler=True,
                    ),
                )
        target = args.root / (name + suffix)
        result = target / "repair-result.json"
        if result.exists():
            require_complete(target)
            snapshot["datasets"][index].update(status(target / "repair-progress.json"))
            continue
        if (setup / "STOP").exists():
            snapshot.update(state="stopped", updated=time.time())
            write_json(setup / "campaign.json", snapshot)
            telemetry.update(snapshot)
            telemetry.finish()
            return 0
        command = [
            sys.executable,
            "-B",
            "-u",
            "-m",
            "research.bar_gpt.v5.quote_repair_v1",
            "--source",
            str(args.root / name),
            "--output",
            str(target),
            "--source-revision",
            args.source_revision,
            "--workers",
            str(args.workers),
        ]
        if args.source_first:
            command += [
                "--source-first",
                "--repaired-base",
                str(args.root / (DATASETS[0][1] + suffix)),
            ]
            reuse = args.root / (name + "-quote-v2")
            if (reuse / "repair-contract.json").exists():
                command += ["--reuse-root", str(reuse)]
        with (setup / (label + ".stdout.log")).open("a") as out, (
            setup / (label + ".stderr.log")
        ).open("a") as err:
            child = subprocess.Popen(command, stdout=out, stderr=err, env=environment)
            write_json(
                setup / "child.json",
                dict(
                    pid=child.pid, started=time.time(), command=command, dataset=label
                ),
            )
            while child.poll() is None:
                current = status(target / "repair-progress.json")
                snapshot["datasets"][index].update(current)
                snapshot.update(updated=time.time(), active=label)
                write_json(setup / "campaign.json", snapshot)
                telemetry.update(snapshot)
                if (setup / "STOP").exists():
                    target.mkdir(exist_ok=True)
                    (target / "STOP").write_text("Campaign graceful stop requested")
                time.sleep(5)
        snapshot["datasets"][index].update(status(target / "repair-progress.json"))
        if child.returncode or not result.exists():
            stopped = (
                (target / "STOP").exists()
                or (setup / "STOP").exists()
                or status(target / "repair-progress.json").get("state") == "stopped"
            )
            snapshot.update(
                state="stopped" if stopped else "failed",
                updated=time.time(),
                failure=(
                    None
                    if stopped
                    else f"{label}: repair child exited {child.returncode}; inspect its stderr and receipt ledger"
                ),
            )
            write_json(setup / "campaign.json", snapshot)
            telemetry.update(snapshot)
            telemetry.finish()
            return 0 if stopped else 1
        require_complete(target)
    snapshot.update(state="complete", updated=time.time(), active=None)
    write_json(setup / "campaign.json", snapshot)
    telemetry.update(snapshot)
    telemetry.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
