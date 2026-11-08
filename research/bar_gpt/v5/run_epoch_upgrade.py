"""Apply a telemetry-only source upgrade after a fully saved epoch, then resume.

The existing controller's ordinary checkpoint-retry path owns the new training
child. No model weights, data split, optimizer, scheduler or completed epochs are
changed. A human STOP or failed learning review cancels this operational upgrade.
"""

import argparse
import ctypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.overnight_v1 import read, metadata_io
from research.bar_gpt.v5.shards.phase1_contract import write_json


def boundary_ready(status, review, results, epoch):
    """Accept only a complete healthy epoch, before meaningful next-epoch work."""
    history = results.get("history", [])
    if not history or history[-1]["epoch"] != epoch:
        return False
    if review.get("epoch") != epoch or review.get("action") != "continue":
        return False
    return status.get("epoch") in (epoch, epoch + 1) and status.get("stage") in {
        "saving epoch checkpoint",
        "starting train loader",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--old-revision", required=True)
    parser.add_argument("--new-revision", required=True)
    args = parser.parse_args(argv)
    root = args.output.resolve()
    runtime = Path("D:/TradingML/runtimes/bar_gpt").resolve()
    if not root.is_relative_to(runtime) or not root.is_dir() or args.epoch < 1:
        raise ValueError(
            "Use the existing workstation campaign root and a positive epoch"
        )
    if not all(
        re.fullmatch("[0-9a-f]{9,40}", revision)
        for revision in (args.old_revision, args.new_revision)
    ):
        raise ValueError("Require explicit committed source identities")
    import msvcrt
    import psutil

    train = root / "train-01"
    evidence = root / "telemetry-upgrade.json"
    marker = "Owned telemetry upgrade at epoch " + str(args.epoch)
    with (root / "telemetry-upgrade.lock").open("a+b") as lock:
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        execution = ctypes.windll.kernel32.SetThreadExecutionState
        execution.argtypes = [ctypes.c_uint]
        execution.restype = ctypes.c_uint
        if not execution(0x80000001):
            raise OSError("Cannot hold workstation awake during source handoff")
        try:
            started = time.monotonic()
            while time.monotonic() - started < 3 * 3600:
                if (root / "STOP").exists() or (train / "STOP").exists():
                    write_json(
                        evidence,
                        dict(state="cancelled", reason="preserved existing STOP"),
                    )
                    return 0
                status, review = read(train / "progress.json"), read(
                    train / "learning-review.json"
                )
                if review.get("action") == "stop":
                    write_json(
                        evidence,
                        dict(
                            state="cancelled", reason="learning guard stopped training"
                        ),
                    )
                    return 0
                if boundary_ready(
                    status, review, read(train / "results.json"), args.epoch
                ):
                    break
                write_json(
                    evidence,
                    dict(
                        state="waiting_epoch_boundary",
                        epoch=args.epoch,
                        observed_epoch=status.get("epoch"),
                        updated=time.time(),
                    ),
                )
                time.sleep(2)
            else:
                raise TimeoutError(
                    "No safe complete-epoch boundary reached within three hours"
                )
            checkpoint = train / f"checkpoint_epoch_{args.epoch:03d}.pt"
            if not checkpoint.is_file():
                raise ValueError("Complete epoch checkpoint is missing")
            owner = read(root / "process.json")
            child = read(root / "train-01-child.json")
            process = psutil.Process(owner["pid"])
            if (
                "overnight_v1.py" not in " ".join(process.cmdline())
                or owner["source_revision"] != args.old_revision
                or root.as_posix().lower()
                not in " ".join(process.cmdline()).replace("\\", "/").lower()
            ):
                raise ValueError("Controller source or process identity changed")
            if read(root / "progress.json").get("stage") != "train-01":
                raise ValueError("Controller changed its owned training attempt")
            if child.get("pid") and psutil.pid_exists(child["pid"]):
                child_process = psutil.Process(child["pid"])
                if (
                    "train-01" not in " ".join(child_process.cmdline())
                    or abs(child_process.create_time() - child["created"]) > 1
                ):
                    raise ValueError("Training child ownership changed")
            write_json(
                evidence,
                dict(
                    state="draining",
                    checkpoint=str(checkpoint),
                    old_revision=args.old_revision,
                    new_revision=args.new_revision,
                    updated=time.time(),
                ),
            )
            task = "BarGPT-V6-Overnight-20261108"
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Disable-ScheduledTask -TaskName '{task}' | Out-Null",
                ],
                check=True,
                capture_output=True,
            )
            # Exclusive creation cannot overwrite a human STOP arriving now.
            with (root / "STOP").open("x", encoding="utf-8") as stop:
                stop.write(marker)
            try:
                process.wait(timeout=300)
            except psutil.TimeoutExpired:
                raise RuntimeError(
                    "Old controller did not drain; preserve STOP and checkpoint"
                ) from None
            if psutil.pid_exists(child["pid"]):
                raise RuntimeError(
                    "Old training child remains alive; do not start another"
                )
            if (root / "STOP").read_text(encoding="utf-8") != marker:
                raise RuntimeError("STOP ownership changed; preserve it")
            if (train / "STOP").exists() and (train / "STOP").read_text(
                encoding="utf-8"
            ) != "Controller stop or 20-minute progress stall":
                raise RuntimeError("Training STOP changed; preserve it")
            # Keep the immutable epoch file and the operational last alias in
            # agreement before asking the normal retry path to resume that alias.
            import torch

            torch.set_num_threads(2)
            saved = torch.load(
                train / "checkpoint_last.pt", map_location="cpu", weights_only=False
            )
            if (
                saved["epoch"] != args.epoch
                or saved["phase"] != "train"
                or saved["step"] != saved["scheduler"]["updates"]
            ):
                raise ValueError("Last checkpoint is not the intended complete epoch")
            write_json(
                root / "telemetry-upgrade-old-progress.json",
                read(train / "progress.json"),
            )
            stopped = read(train / "progress.json")
            stopped.update(
                state="stopped",
                stage="complete-epoch telemetry upgrade",
                updated=time.time(),
            )
            write_json(train / "progress.json", stopped)
            for name in ("controller.stderr.log", "controller.stdout.log"):
                shutil.copyfile(
                    root / name, root / ("before-telemetry-upgrade-" + name)
                )
            launcher = root / "run.ps1"
            text = launcher.read_text()
            if args.old_revision not in text:
                raise ValueError("Launcher no longer pins the expected old source")
            launcher.write_text(
                text.replace(args.old_revision, args.new_revision), encoding="utf-8"
            )
            code = Path(__file__).resolve().parents[3]

            def quote(value):
                return "'" + str(value).replace("'", "''") + "'"

            argument = f'-NoProfile -ExecutionPolicy Bypass -File "{launcher}"'
            script = f"$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument {quote(argument)} -WorkingDirectory {quote(code)}; Set-ScheduledTask -TaskName '{task}' -Action $a | Out-Null; Enable-ScheduledTask -TaskName '{task}' | Out-Null; Start-ScheduledTask -TaskName '{task}'"
            (
                root / "STOP"
            ).unlink()  # Remove only the exact owned marker checked above.
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                check=True,
                capture_output=True,
            )
            write_json(
                evidence,
                dict(
                    state="resumed",
                    epoch=args.epoch,
                    checkpoint=str(checkpoint),
                    updates=saved["step"],
                    new_revision=args.new_revision,
                    updated=time.time(),
                ),
            )
            return 0
        finally:
            execution(0x80000000)


if __name__ == "__main__":
    raise SystemExit(main())
