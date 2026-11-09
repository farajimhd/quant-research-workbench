"""Apply the v3 opening-spread guard after each v2 source repair completes.

The existing canonical-rebuild campaign keeps its immutable snapshot and does
not restart. This controller waits for each complete v2 release, then performs
one hash-verified CPU/I/O pass into a separate v3 root, without ClickHouse reads.
It never signals the trainer or resumes a market-data producer.
"""

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time

from .quote_repair_campaign_v1 import DATASETS, RepairTelemetry, status
from .quote_repair_v1 import require_complete
from .shards.contract import digest, write_json

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def publish_cohort(root):
    """Seal completed v3 members; retain original names and selection contracts."""
    for _, name in DATASETS[:2]:
        require_complete(root / (name + "-quote-v3"))
    old = status(root / "phase1-v6-cohort/cohort.json")
    body = dict(old["cohort"])
    body["cohort_id"] += "-quote-v3"
    body["members"] = [
        dict(name=item["name"], path="../" + DATASETS[index][1] + "-quote-v3")
        for index, item in enumerate(body["members"])
    ]
    cohort = dict(cohort=body, hash=digest(body))
    path = root / "phase1-v6-cohort-quote-v3/cohort.json"
    path.parent.mkdir(exist_ok=True)
    write_json(path, cohort)
    write_json(root / (DATASETS[0][1] + "-quote-v3") / "cohort.json", cohort)
    return path


def main(argv=None):
    """Wait without resource use; preserve receipts and stop at owned boundaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt")
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if not args.root.is_dir() or not 1 <= args.workers <= 16:
        raise ValueError("Require the existing runtime root and bounded workers")
    setup = args.root / "quote-guard-setup"
    setup.mkdir(exist_ok=True)
    import msvcrt

    owner = (setup / "campaign.lock").open("a+b")
    owner.write(b"0")
    owner.flush()
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    # Hold system wakefulness if the independent learning guard stops training
    # while released-data migration is still running; do not force the display.
    keep_awake = ctypes.windll.kernel32.SetThreadExecutionState
    keep_awake.argtypes = [ctypes.c_uint]
    keep_awake.restype = ctypes.c_uint
    if not keep_awake(0x80000001):
        raise OSError("Cannot hold workstation awake for the guard migration")
    state = dict(
        state="waiting",
        pid=os.getpid(),
        started=time.time(),
        revision=args.source_revision,
        policy_suffix="-quote-v3",
        datasets=[
            dict(name=label, root=name + "-quote-v3", state="queued")
            for label, name in DATASETS
        ],
    )
    write_json(setup / "process.json", state)
    telemetry = RepairTelemetry(
        setup,
        dict(
            job="quote-opening-guard-v3",
            workers=args.workers,
            source_revision=args.source_revision,
        ),
        project="bargpt-quote-repair-v1",
    )
    state["wandb_url"] = telemetry.url
    try:
        for index, (label, name) in enumerate(DATASETS):
            if (setup / "STOP").exists():
                state.update(state="stopped", updated=time.time())
                return 0
            source = args.root / (name + "-quote-v2")
            target = args.root / (name + "-quote-v3")
            while not (source / "repair-result.json").exists():
                if (setup / "STOP").exists():
                    state.update(state="stopped", updated=time.time())
                    return 0
                state.update(
                    state="waiting for immutable v2 source",
                    active=label,
                    updated=time.time(),
                )
                write_json(setup / "campaign.json", state)
                telemetry.update(state)
                time.sleep(5)
            require_complete(source)
            if not (target / "repair-result.json").exists():
                command = [
                    sys.executable,
                    "-B",
                    "-u",
                    "-m",
                    "research.bar_gpt.v5.quote_repair_v1",
                    "--source",
                    str(source),
                    "--output",
                    str(target),
                    "--source-revision",
                    args.source_revision,
                    "--workers",
                    str(args.workers),
                    "--bar-guard-only",
                    "--repaired-base",
                    str(args.root / (DATASETS[0][1] + "-quote-v3")),
                ]
                with (setup / (label + ".stdout.log")).open("a") as out, (
                    setup / (label + ".stderr.log")
                ).open("a") as err:
                    child = subprocess.Popen(command, stdout=out, stderr=err)
                    write_json(
                        setup / "child.json",
                        dict(
                            pid=child.pid,
                            started=time.time(),
                            command=command,
                            dataset=label,
                        ),
                    )
                    while child.poll() is None:
                        state["datasets"][index].update(
                            status(target / "repair-progress.json")
                        )
                        state.update(state="running", active=label, updated=time.time())
                        write_json(setup / "campaign.json", state)
                        telemetry.update(state)
                        if (setup / "STOP").exists() and not (target / "STOP").exists():
                            target.mkdir(parents=True, exist_ok=True)
                            (target / "STOP").write_text(
                                "Owned opening-guard campaign stop"
                            )
                        time.sleep(5)
                if child.returncode or not (target / "repair-result.json").exists():
                    state.update(
                        state=(
                            "stopped"
                            if (setup / "STOP").exists() or (target / "STOP").exists()
                            else "failed"
                        ),
                        updated=time.time(),
                    )
                    return 0 if state["state"] == "stopped" else 1
            require_complete(target)
            state["datasets"][index].update(state="complete")
            if index == 1:
                cohort = publish_cohort(args.root)
                write_json(
                    args.root / "quote-repair-setup/training-handoff-needed.json",
                    dict(
                        state="audited_handoff_required",
                        cohort=str(cohort),
                        campaign="overnight-v6-20261108",
                        checkpoint_boundary_only=True,
                        wandb_id="1rpdtas2",
                        preserve_optimizer_scheduler=True,
                        policy="quote-v3",
                    ),
                )
        state.update(state="complete", updated=time.time())
        return 0
    except Exception as error:
        state.update(state="failed", failure=str(error), updated=time.time())
        raise
    finally:
        write_json(setup / "campaign.json", state)
        telemetry.update(state)
        telemetry.finish()
        keep_awake(0x80000000)
        owner.close()


if __name__ == "__main__":
    raise SystemExit(main())
