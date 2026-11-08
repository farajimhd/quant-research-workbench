"""Durable local metrics, W&B uploads, GPU sampling, and a low-rate status heartbeat."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from research.mlops.wandb_utils import init_wandb
from .shards.contract import write_json


class Reporter:
    """One controller owns metrics; a bounded thread owns progress publication and GPU polling."""

    def __init__(
        self, root: Path, config: dict, mode="online", project="bargpt-column-mae-v1"
    ):
        self.root, self.lock, self.stop = root, threading.Lock(), threading.Event()
        self.state = dict(
            state="running", stage="initializing", done=0, total=0, results=[]
        )
        self.gpu_samples = []
        self.run = init_wandb(
            entity=os.environ.get("WANDB_ENTITY", ""),
            project=project,
            run_name=root.name,
            config=config,
            run_dir=root,
            mode=mode,
            timeout_seconds=30,
            capture_console=False,
        )
        self.state["wandb_url"] = getattr(self.run, "url", None)
        if self.run:
            self.run.define_metric("global_step")
            self.run.define_metric("*", step_metric="global_step")
        # Windows file-open/sharing checks grow costly as the journal grows.
        # Hold one line-buffered handle: each record remains immediately visible
        # before upload, without reopening the file for every delayed loss row.
        self.journal = (self.root / "metrics.jsonl").open(
            "a", encoding="utf-8", buffering=1
        )
        if self.run:
            # W&B's file service handles flushed journal changes asynchronously.
            self.run.save(
                str(self.root / "metrics.jsonl"),
                base_path=str(self.root),
                policy="live",
            )
        self.thread = threading.Thread(
            target=self._observe, name="mae-status", daemon=True
        )
        self.thread.start()

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def _observe(self):
        while not self.stop.is_set():
            try:
                process = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,memory.used,power.draw",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if process.returncode == 0:
                    util, memory, power = map(
                        float, process.stdout.splitlines()[0].split(",")
                    )
                    with self.lock:
                        self.gpu_samples.append(
                            (time.perf_counter(), util, memory, power)
                        )
                        self.state.update(
                            gpu_util=util, gpu_memory_mib=memory, gpu_power_w=power
                        )
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.update(gpu_telemetry="unavailable")
            self.publish()
            self.stop.wait(1)

    def publish(self):
        workers = []
        for file in (self.root / "workers").glob("*.json"):
            try:
                workers.append(json.loads(file.read_text()))
            except (OSError, ValueError):
                pass
        with self.lock:
            snapshot = dict(
                self.state,
                updated=time.time(),
                workers=sorted(workers, key=lambda item: item["id"]),
            )
        try:
            write_json(self.root / "progress.json", snapshot)
        except OSError as error:
            print(f"Progress publication failed: {type(error).__name__}", flush=True)

    def log(self, metrics: dict, step: int):
        """Append exact local evidence before uploading; W&B errors are not silently ignored."""
        self.journal.write(
            json.dumps(dict(step=step, **metrics), allow_nan=False) + "\n"
        )
        if self.run:
            # A separate W&B row index permits delayed loss uploads and validation at a fixed train step.
            self.run.log(dict(metrics, global_step=step))

    def finish(self, state="complete", error=None):
        self.stop.set()
        self.thread.join(timeout=15)
        if not self.journal.closed:
            self.journal.close()  # Flush/close before artifacts read the final local evidence.
        self.update(state=state, error=error)
        self.publish()
        if self.run:
            import wandb

            artifact = wandb.Artifact(
                self.root.name + "-results", type="profiling-results"
            )
            for name in (
                "results.json",
                "manifest.json",
                "metrics.jsonl",
                "calibration.json",
            ):
                if (self.root / name).exists():
                    artifact.add_file(str(self.root / name))
            self.run.log_artifact(artifact)
            self.run.finish(exit_code=0 if state == "complete" else 1)


def open_panel(root: Path):
    """Open the explicitly requested visible observer in the workstation user's desktop session."""

    def quote(value):
        return (
            "'" + str(value).replace("'", "''") + "'"
        )  # PowerShell literal, never shell substitution.

    repository = Path(__file__).resolve().parents[3]
    script = root / "open-progress.ps1"
    script.write_text(
        "$env:PYTHONDONTWRITEBYTECODE='1'\n$env:PYTHONIOENCODING='utf-8'\n"
        "$Host.UI.RawUI.WindowTitle='BarGPT MAE profiling and training'\n"
        f"Set-Location -LiteralPath {quote(repository)}\n"
        f"& {quote(sys.executable)} -B -u -m research.bar_gpt.v5.progress_v1 --run {quote(root)}\n",
        encoding="utf-8",
    )
    name = "BarGPT-MAE-" + root.name
    arguments = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}"'
    command = (
        f"$a=New-ScheduledTaskAction -Execute 'conhost.exe' -Argument {quote(arguments)}; "
        "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive; "
        f"Register-ScheduledTask -TaskName {quote(name)} -Action $a -Principal $p -Force | Out-Null; "
        f"Start-ScheduledTask -TaskName {quote(name)}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
    )
    return name


def remove_panel_task(name):
    """Remove the one-shot task definition; the observer exits on the final status itself."""
    if name:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, check=True
        )
