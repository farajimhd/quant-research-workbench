"""One-shot authorized v1 drain / fresh v2 launch after full statistics seal.

This is a boundary handoff, not a restart/monitor policy. It never kills a
trainer, clears a STOP, copies model weights, or resumes a learning-guard stop.
"""

import argparse
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from .shards.contract import write_json, digest


def training_command(parent, output, calibration, revision):
    """Preserve current scientific settings; fresh model, new stats, peak=1e-3."""
    command = [
        sys.executable,
        "-B",
        "-u",
        "-m",
        "research.bar_gpt.v5.run_encoder_v2",
        "--output",
        str(output),
        "--source-revision",
        revision,
        "--calibration",
        str(calibration),
        "--mode",
        "train",
    ]
    keys = (
        "dataset",
        "split_manifest",
        "model",
        "precision",
        "attention",
        "attention_dropout",
        "batch_size",
        "workers",
        "prefetch",
        "epochs",
        "train_steps",
        "validation_steps",
        "scheduler",
        "min_learning_rate",
        "warmup_updates",
        "metrics_every",
        "loss_every",
        "checkpoint_every",
        "seed",
        "wandb_mode",
        "wandb_project",
        "guard_probe_steps",
        "overfit_patience",
    )
    for key in keys:
        command.extend(["--" + key.replace("_", "-"), str(parent[key])])
    command.extend(["--learning-rate", "0.001"])
    if parent.get("learning_guard"):
        command.append("--learning-guard")
    if parent.get("panel"):
        command.append("--panel")
    return command


def owned_process(record, required):
    """Fresh PID + creation + command ownership, not a historical PID assumption."""
    try:
        process = psutil.Process(record["pid"])
    except psutil.NoSuchProcess:
        return None
    if abs(process.create_time() - record["created"]) > 0.1:
        raise ValueError("Process ID was reused; ownership failed")
    command = " ".join(process.cmdline())
    if required not in command:
        raise ValueError("Process command no longer matches the authorized owner")
    return process


def verified_early_drain(path, parent):
    """Accept only a sealed owned drain with intact checkpoint and absent owners."""
    record = json.loads(Path(path).read_text())
    if record.get("hash") != digest({k: v for k, v in record.items() if k != "hash"}):
        raise ValueError("Early-drain receipt seal changed")
    if (
        record.get("state") != "drained"
        or Path(record["parent"]) != parent
        or not record["marker"].startswith("OWNED_ENCODER_V2_ZSCORE_FRESH_START:")
        or (parent / "STOP").read_text().strip() != record["marker"]
    ):
        raise ValueError("Human or foreign STOP; early-drain ownership failed")
    for owner in (record["child"], record["controller"]):
        try:
            p = psutil.Process(owner["pid"])
            if abs(p.create_time() - owner["created"]) < 0.1 and p.is_running():
                raise ValueError("Early-drain owner is still running")
        except psutil.NoSuchProcess:
            pass
    checkpoint = parent / "train-07/checkpoint_last.pt"
    if (
        Path(record["checkpoint"]) != checkpoint
        or hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        != record["checkpoint_sha256"]
    ):
        raise ValueError("Early-drain checkpoint changed")
    return record["marker"]


def main(argv=None):
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    setup, parent_root, output = args.setup, args.parent, args.output
    metadata = json.loads((parent_root / "train-07/manifest.json").read_text())
    proof = json.loads((setup / "training-snapshot.json").read_text())
    revision = proof["revision"]
    source = Path(proof["target"])
    if output.exists():
        raise ValueError("Fresh v2 output already exists; refuse duplicate launch")
    # One controller per setup, with creation-time validation on recovery.
    ledger = setup / "handoff-process.json"
    if ledger.exists():
        old = json.loads(ledger.read_text())
        if old["pid"] != os.getpid() and owned_process(old, "run_zscore_upgrade_v2"):
            raise ValueError("A handoff controller already owns this setup")
    write_json(
        ledger,
        dict(pid=os.getpid(), created=psutil.Process().create_time(), command=sys.argv),
    )
    status = setup / "handoff-progress.json"
    awake = os.name == "nt"
    if awake and not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
        raise RuntimeError("Could not hold keep-awake request")
    try:
        calibration = setup / "statistics/calibration.json"
        while not calibration.exists():
            if (setup / "STOP").exists():
                raise InterruptedError("Human handoff STOP; old training untouched")
            state_file = setup / "statistics/progress.json"
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            write_json(
                status,
                dict(
                    state="waiting-statistics",
                    statistics=state,
                    old_training_untouched=True,
                    updated=time.time(),
                ),
            )
            exit_file = setup / "fit-exit.json"
            if (
                exit_file.exists()
                and json.loads(exit_file.read_text())["exit_code"] != 0
            ):
                raise RuntimeError("Statistics failed; old training untouched")
            time.sleep(10)
        for name, item in proof["files"].items():
            if (
                hashlib.sha256((source / name).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("Immutable v2 source changed: " + name)
        from .data_v1 import load_split
        from .zscore_calibration_v2 import load_calibration

        manifest = load_split(Path(metadata["split_manifest"]))
        certificate, _ = load_calibration(calibration, manifest)
        if output.exists() or (setup / "STOP").exists():
            raise ValueError("Handoff cancelled or duplicate output")
        progress = json.loads((parent_root / "progress.json").read_text())
        child = progress["child"]
        if Path(child["output"]) != parent_root / "train-07":
            raise ValueError("Parent training attempt changed; review before draining")
        early = setup / "parent-drained.json"
        if early.exists():
            marker = verified_early_drain(early, parent_root)
            trainer = controller = None
        else:
            trainer = owned_process(child, "research.bar_gpt.v5.run_v1")
            controller_ledger = json.loads((parent_root / "process.json").read_text())
            controller = psutil.Process(controller_ledger["pid"])
            if (
                "overnight_v1.py" not in " ".join(controller.cmdline())
                or controller_ledger["source_revision"] != metadata["source_revision"]
                or abs(
                    controller.create_time()
                    - datetime.fromisoformat(
                        controller_ledger["started"].replace("Z", "+00:00")
                    ).timestamp()
                )
                > 2
            ):
                raise ValueError("Parent controller ownership changed")
            if (parent_root / "STOP").exists() or (
                parent_root / "train-07/STOP"
            ).exists():
                raise ValueError(
                    "Existing parent STOP; never overwrite human/guard evidence"
                )
        command = training_command(metadata, output, calibration, revision)
        write_json(
            setup / "handoff-plan.json",
            dict(
                parent=metadata,
                command=command,
                calibration_hash=certificate["hash"],
                version=2,
                fresh_weights=True,
                peak=0.001,
                prior_training_child=child,
                source_revision=revision,
            ),
        )
        if not early.exists():
            marker = "OWNED_ENCODER_V2_ZSCORE_FRESH_START:" + revision
            with (parent_root / "STOP").open("x", encoding="utf-8") as handle:
                handle.write(marker + "\n")
            write_json(
                status,
                dict(
                    state="draining-parent",
                    child=child,
                    marker=marker,
                    updated=time.time(),
                ),
            )
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if not controller.is_running() and (
                    trainer is None or not trainer.is_running()
                ):
                    break
                time.sleep(1)
            else:
                raise TimeoutError(
                    "Parent did not drain; no signal or v2 launch attempted"
                )
        if (setup / "STOP").exists() or (
            parent_root / "STOP"
        ).read_text().strip() != marker:
            raise ValueError("STOP ownership changed; v2 launch cancelled")
        # Short real executable-path smoke only after the old GPU owner has gone.
        pilot = setup / "gpu-path-smoke"
        smoke = training_command(metadata, pilot, calibration, revision)
        for flag, value in (
            ("--epochs", "1"),
            ("--train-steps", "2"),
            ("--validation-steps", "2"),
            ("--batch-size", "64"),
            ("--workers", "1"),
            ("--guard-probe-steps", "1"),
            ("--wandb-mode", "disabled"),
        ):
            smoke[smoke.index(flag) + 1] = value
        if "--panel" in smoke:
            smoke.remove("--panel")
        env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1")
        with (setup / "smoke.stdout.log").open("w") as stdout, (
            setup / "smoke.stderr.log"
        ).open("w") as stderr:
            subprocess.run(
                smoke,
                cwd=source,
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=True,
                timeout=180,
            )
        if (setup / "STOP").exists():
            raise InterruptedError("Human STOP after smoke")
        with (setup / "training.stdout.log").open("w") as stdout, (
            setup / "training.stderr.log"
        ).open("w") as stderr:
            process = subprocess.Popen(
                command,
                cwd=source,
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            write_json(
                setup / "v2-child.json",
                dict(
                    pid=process.pid,
                    created=psutil.Process(process.pid).create_time(),
                    command=command,
                ),
            )
            write_json(
                status,
                dict(
                    state="v2-running",
                    pid=process.pid,
                    output=str(output),
                    updated=time.time(),
                ),
            )
            code = process.wait()
        write_json(
            status,
            dict(
                state="v2-exited",
                exit_code=code,
                output=str(output),
                updated=time.time(),
            ),
        )
        if code:
            raise RuntimeError("V2 exited with failure; checkpoints preserved")
    except BaseException as error:
        write_json(status, dict(state="failed", error=str(error), updated=time.time()))
        raise
    finally:
        if awake:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
