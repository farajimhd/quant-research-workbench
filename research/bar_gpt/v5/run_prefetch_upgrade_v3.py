"""Resume the same v3 experiment at its complete-training checkpoint boundary."""

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import torch

from .shards.contract import write_json


def boundary_ready(saved, status):
    history = saved.get("history", [])
    previous_ok = (
        not history or history[-1].get("learning_guard", {}).get("action") == "continue"
    )
    return (
        saved.get("phase") == "validation"
        and saved.get("step", 0) > 0
        and saved["epoch"] + 1 == status.get("epoch")
        and saved.get("training_metrics", {}).get("complete_pass") is True
        and math.isfinite(saved["training_metrics"]["loss"])
        and saved["scheduler"]["updates"] == saved["step"]
        and previous_ok
    )


def resume_command(manifest, checkpoint, output, revision, prefetch=2):
    command = [
        sys.executable,
        "-B",
        "-u",
        "-m",
        "research.bar_gpt.v5.run_encoder_v3",
        "--output",
        str(output),
        "--source-revision",
        revision,
        "--resume",
        str(checkpoint),
        "--prefetch",
        str(prefetch),
        "--shard-prefetch",
        "32",
        "--shard-prefetch-mib",
        "128",
    ]
    for key in (
        "mode",
        "models",
        "dataset",
        "split_manifest",
        "model",
        "precision",
        "attention",
        "attention_dropout",
        "batch_size",
        "workers",
        "epochs",
        "train_steps",
        "validation_steps",
        "scheduler",
        "learning_rate",
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
    ):
        command.extend(["--" + key.replace("_", "-"), str(manifest[key])])
    if manifest.get("calibration"):
        command.extend(["--calibration", manifest["calibration"]])
    if manifest.get("learning_guard"):
        command.append("--learning-guard")
    if manifest.get("panel"):
        command.append("--panel")
    return command


def main(argv=None):
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("setup", "parent", "output", "owner", "profile-script"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args(argv)
    setup, parent, output = args.setup, args.parent, args.output
    proof = json.loads((setup / "snapshot.json").read_text())
    source = Path(proof["target"])
    revision = proof["revision"]
    if output.exists():
        raise ValueError("Resume output already exists")
    for name, item in proof["files"].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Snapshot drift: " + name)
    owner = json.loads(args.owner.read_text())
    old = psutil.Process(owner["pid"])
    if abs(old.create_time() - owner["created"]) > 0.1 or str(parent) not in " ".join(
        old.cmdline()
    ):
        raise ValueError("Trainer ownership changed")
    manifest = json.loads((parent / "manifest.json").read_text())
    checkpoint = parent / "checkpoint_last.pt"
    write_json(
        setup / "upgrade-owner.json",
        dict(pid=os.getpid(), created=psutil.Process().create_time(), command=sys.argv),
    )
    assert ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        while True:
            if (setup / "STOP").exists() or (parent / "STOP").exists():
                raise InterruptedError("Human STOP; no handoff")
            if not old.is_running():
                raise RuntimeError("Original trainer exited; no blind restart")
            status = json.loads((parent / "progress.json").read_text())
            write_json(
                setup / "upgrade-progress.json",
                dict(
                    state="waiting-complete-training-boundary",
                    epoch=status.get("epoch"),
                    done=status.get("done"),
                    total=status.get("total"),
                    updated=time.time(),
                ),
            )
            if status.get("stage") in (
                "starting validation loader",
                "measuring validation",
                "validation",
            ):
                saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
                if boundary_ready(saved, status):
                    break
            time.sleep(2)
        sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        step = saved["step"]
        marker = "OWNED_VERIFIED_SHARD_PREFETCH_V3:" + revision + ":" + str(step)
        with (parent / "STOP").open("x", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        write_json(
            setup / "upgrade-progress.json",
            dict(
                state="draining-at-validation-boundary", step=step, updated=time.time()
            ),
        )
        deadline = time.monotonic() + 120
        while old.is_running() and time.monotonic() < deadline:
            time.sleep(1)
        if old.is_running():
            raise TimeoutError("Trainer did not drain; no signals sent")
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != sha:
            raise ValueError("Boundary checkpoint changed")
        write_json(
            setup / "boundary-proof.json",
            dict(
                parent=str(parent),
                checkpoint=str(checkpoint),
                sha256=sha,
                step=step,
                epoch=saved["epoch"],
                phase=saved["phase"],
                scheduler_updates=saved["scheduler"]["updates"],
                marker=marker,
            ),
        )
        del saved
        env = dict(
            os.environ,
            PYTHONPATH=str(source),
            PYTHONDONTWRITEBYTECODE="1",
            WANDB_ENTITY="mehdifaraji",
        )
        write_json(
            setup / "upgrade-progress.json",
            dict(
                state="bounded-prefetch-qualification", step=step, updated=time.time()
            ),
        )
        with (setup / "qualification.stdout.log").open("w") as stdout, (
            setup / "qualification.stderr.log"
        ).open("w") as stderr:
            subprocess.run(
                [sys.executable, "-B", "-u", str(args.profile_script)],
                cwd=source,
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=True,
                timeout=240,
            )
        result = json.loads((setup / "qualification.json").read_text())
        if not result["parity"] or not result["qualified"]:
            raise ValueError("Prefetch qualification failed; checkpoint preserved")
        if (setup / "STOP").exists() or (parent / "STOP").read_text().strip() != marker:
            raise InterruptedError("STOP ownership changed")
        command = resume_command(
            manifest, checkpoint, output, revision, result.get("prefetch", 2)
        )
        write_json(
            setup / "resume-plan.json",
            dict(
                command=command,
                checkpoint_sha256=sha,
                step=step,
                preserve_wandb=manifest.get("wandb_identity"),
                source_revision=revision,
            ),
        )
        with (setup / "training.stdout.log").open("w") as stdout, (
            setup / "training.stderr.log"
        ).open("w") as stderr:
            child = subprocess.Popen(
                command,
                cwd=source,
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            write_json(
                setup / "child.json",
                dict(
                    pid=child.pid,
                    created=psutil.Process(child.pid).create_time(),
                    command=command,
                    output=str(output),
                ),
            )
            write_json(
                setup / "upgrade-progress.json",
                dict(
                    state="resumed",
                    step=step,
                    pid=child.pid,
                    output=str(output),
                    updated=time.time(),
                ),
            )
            code = child.wait()
        write_json(setup / "exit.json", dict(exit_code=code, finished=time.time()))
        if code:
            raise RuntimeError("Resumed trainer failed; checkpoints retained")
    except BaseException as error:
        write_json(
            setup / "upgrade-progress.json",
            dict(state="failed", error=str(error), updated=time.time()),
        )
        raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
