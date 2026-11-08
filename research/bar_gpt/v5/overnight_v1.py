"""Restartable workstation handoff: completed shards -> audit -> profile -> train.

This is a task-owned process, not a calendar automation. Its OS lock prevents
duplicate controllers. Child ownership, progress, exit evidence and bounded
retries are durable under --output. It never changes canonical bars or model
architecture, and never trains against a still-growing inventory.
"""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.shards.phase1_contract import write_json


def read(path):
    """Read atomic runtime evidence; absence is explicit, not a successful state."""
    return json.loads(path.read_text()) if path.exists() else {}


class Controller:
    """Own one auditable overnight handoff, with no dependency on a laptop session."""

    def __init__(self, args):
        self.args, self.root = args, args.output
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = read(self.root / "progress.json")

    def update(self, **values):
        self.state.update(
            values, updated=time.time(), source_revision=self.args.source_revision
        )
        write_json(self.root / "progress.json", self.state)

    def stopped(self):
        return (self.root / "STOP").exists()

    def wait_for_shards(self):
        """Require final counters AND released producer locks for both cohort members."""
        from research.bar_gpt.v5.cohort_v1 import members
        from research.bar_gpt.v5.run_liquid_supplement import dependency_ready

        began = time.monotonic()
        first_failure = {}
        while not self.stopped():
            _, components = members(self.args.dataset)
            locks, ready = [], True
            statuses = {}
            try:
                for name, root in components:
                    status = read(root / "progress.json")
                    statuses[name] = {
                        key: status.get(key)
                        for key in (
                            "state",
                            "samples",
                            "bytes",
                            "bytes_per_second",
                            "frames",
                            "failed",
                            "updated",
                        )
                    }
                    if status.get("state") == "failed":
                        first_failure.setdefault(name, time.monotonic())
                        if time.monotonic() - first_failure[name] > 120:
                            if not self.retry_shards(name, root, status):
                                raise RuntimeError(
                                    f"Shard producer {name} failed: {status.get('failed')}"
                                )
                            first_failure[name] = time.monotonic()
                    else:
                        first_failure.pop(name, None)
                    owner = dependency_ready(root)
                    if owner is None:
                        ready = False
                    else:
                        locks.append(owner)
                self.update(
                    state="running",
                    stage="waiting for complete shard cohort",
                    shards=statuses,
                )
                if ready and len(components) == 2:
                    return
            finally:
                for owner in locks:
                    owner.close()
            time.sleep(15)
        raise InterruptedError("Overnight handoff was stopped")

    def retry_shards(self, name, root, status):
        """Resume only known transient producer failures, never override a user's STOP."""
        failure = json.dumps(status.get("failed", []))
        transient = any(
            term in failure.lower()
            for term in (
                "timeout",
                "connection",
                "socket",
                "503",
                "502",
                "temporarily",
                "memory limit",
            )
        )
        if not transient or (root / "STOP").exists():
            return False
        path = self.root / "shard-retries.json"
        retries = read(path)
        if retries.get(name, 0) >= 2:
            return False
        task = {
            "base": "BarGPT-Shards-2019-2025-v6",
            "liquid500": "BarGPT-Shards-v6-Liquid500",
        }[name]
        # Static owned task names; no event data or user strings enter shell code.
        command = f"$t=Get-ScheduledTask -TaskName '{task}'; if ($t.State -ne 'Running') {{ Start-ScheduledTask -TaskName '{task}' }}"
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            timeout=30,
        )
        retries[name] = retries.get(name, 0) + 1
        write_json(path, retries)
        self.update(
            stage="retrying transient shard failure",
            retry_member=name,
            retry_count=retries[name],
        )
        return True

    def child(self, label, arguments):
        """Start/reconnect one owned child; monitor progress without blocking the UI.

        Reconnection verifies PID creation time and command-line output identity.
        A twenty-minute lack of numerical progress requests a graceful stop.
        A genuinely hung child gets two minutes to drain before its owned process
        tree is terminated. No unrelated workstation processes are touched.
        """
        import psutil

        ledger = self.root / (label + "-child.json")
        saved = read(ledger)
        output = self.root / label
        if (
            saved.get("exit_code") == 0
            and read(output / "progress.json").get("state") == "complete"
        ):
            return output
        process = None
        if saved.get("pid"):
            try:
                candidate = psutil.Process(saved["pid"])
                if abs(candidate.create_time() - saved["created"]) < 1 and str(
                    output
                ) in " ".join(candidate.cmdline()):
                    process = candidate
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if process is None:
            if output.exists():
                return output  # The caller diagnoses terminal evidence before choosing a fresh attempt.
            command = [
                sys.executable,
                "-B",
                "-u",
                "-m",
                "research.bar_gpt.v5.run_v1",
                "--output",
                str(output),
                "--source-revision",
                self.args.source_revision,
                *arguments,
            ]
            with (self.root / (label + ".stdout.log")).open("ab") as stdout, (
                self.root / (label + ".stderr.log")
            ).open("ab") as stderr:
                launched = subprocess.Popen(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            process = psutil.Process(launched.pid)
            saved = dict(
                pid=process.pid,
                created=process.create_time(),
                command=command,
                output=str(output),
                started=time.time(),
            )
            write_json(ledger, saved)
        last_marker, changed, stop_sent = None, time.monotonic(), None
        while process.is_running():
            status = read(output / "progress.json")
            marker = (
                status.get("stage"),
                status.get("epoch"),
                status.get("done"),
                status.get("total"),
            )
            if marker != last_marker:
                last_marker, changed = marker, time.monotonic()
            self.update(
                state="running",
                stage=label,
                child=saved,
                training={
                    key: status.get(key)
                    for key in (
                        "stage",
                        "epoch",
                        "epochs",
                        "done",
                        "total",
                        "metrics",
                        "gpu_util",
                        "wandb_url",
                    )
                },
            )
            if self.stopped() or time.monotonic() - changed > 1200:
                if output.is_dir() and stop_sent is None:
                    (output / "STOP").write_text(
                        "Controller stop or 20-minute progress stall", encoding="utf-8"
                    )
                    stop_sent = time.monotonic()
                if stop_sent and time.monotonic() - stop_sent > 120:
                    # Revalidate process ownership before terminating only its tree.
                    if abs(process.create_time() - saved["created"]) < 1:
                        for child in process.children(recursive=True):
                            try:
                                child.terminate()
                            except psutil.NoSuchProcess:
                                pass
                        process.terminate()
                    break
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                continue
            break
        status = read(output / "progress.json")
        saved.update(
            finished=time.time(),
            exit_code=0 if status.get("state") == "complete" else 1,
            final_state=status.get("state"),
            error=status.get("error"),
        )
        write_json(ledger, saved)
        if self.stopped():
            raise InterruptedError("Overnight handoff was stopped")
        return output

    def run(self):
        """Prepare once, run only short diagnostics, then leave a guarded full campaign running."""
        from research.bar_gpt.v5.cohort_audit_v1 import audit
        from research.bar_gpt.v5.data_v1 import build_split, calibrate, load_split
        from research.bar_gpt.v5.run_pretrain import campaign_arguments

        self.wait_for_shards()
        if not (self.root / "cohort-audit.json").exists():
            self.update(stage="auditing completed cohort")
            audit(self.args.dataset, self.root / "cohort-audit.json", self.update)
        manifest_path = self.root / "split.json"
        manifest = (
            load_split(manifest_path)
            if manifest_path.exists()
            else build_split(self.args.dataset, manifest_path, self.update)
        )
        calibration_path = self.root / "calibration.json"
        if not calibration_path.exists():
            self.update(stage="training-only calibration")
            _, calibration = calibrate(manifest)
            write_json(calibration_path, calibration)
        common = [
            "--dataset",
            str(self.args.dataset),
            "--split-manifest",
            str(manifest_path),
            "--calibration",
            str(calibration_path),
            "--precision",
            "bf16",
            "--attention",
            "sdpa",
            "--attention-dropout",
            "0",
            "--seed",
            "17",
            "--wandb-mode",
            "online",
            "--wandb-project",
            "bargpt-v6-latest-bar-overnight",
            "--panel",
        ]
        profile = self.child(
            "profile",
            [
                *common,
                "--mode",
                "profile",
                "--models",
                "large",
                "--batches",
                "4096,2048",
                "--workers",
                "32",
                "--prefetch",
                "2",
                "--steps",
                "48",
                "--warmup",
                "4",
                "--seconds",
                "30",
            ],
        )
        rows = read(profile / "results.json").get("results", [])
        valid = [
            row
            for row in rows
            if row.get("status") == "complete"
            and math.isfinite(row["loss"])
            and row.get("excluded_no_price") == 0
            and all(row["target_counts"])
        ]
        preferred = [row for row in valid if row["batch_size"] == 4096]
        if not valid:
            raise RuntimeError("No valid large/BF16 profiling configuration")
        chosen = max(preferred or valid, key=lambda row: row["samples_per_second"])
        # Keep the agreed 4096/32 configuration whenever it fits; a bounded
        # 2048 fallback is allowed only when the agreed batch failed to fit.
        write_json(self.root / "selected-configuration.json", chosen)
        smoke = self.child(
            "smoke",
            campaign_arguments(
                [
                    *common,
                    "--batch-size",
                    str(chosen["batch_size"]),
                    "--epochs",
                    "1",
                    "--train-steps",
                    "128",
                    "--validation-steps",
                    "16",
                    "--warmup-updates",
                    "16",
                    "--metrics-every",
                    "32",
                ]
            ),
        )
        result = read(smoke / "results.json")
        if read(smoke / "progress.json").get("state") != "complete" or not result.get(
            "history"
        ):
            raise RuntimeError("Short trainer failed; do not start a long campaign")
        for row in result["history"]:
            if not all(
                math.isfinite(row[phase]["loss"])
                and row[phase]["samples"] > 0
                and all(row[phase]["target_counts"])
                for phase in ("training", "validation")
            ):
                raise RuntimeError(
                    "Short trainer has missing targets or nonfinite evidence"
                )
        if not (smoke / "checkpoint_last.pt").exists():
            raise RuntimeError(
                "Short trainer failed to save resumable optimizer/scheduler state"
            )
        previous, peak, learning_repaired = None, 1e-3, False
        for attempt in range(3):
            label = f"train-{attempt + 1:02d}"
            arguments = campaign_arguments(
                [
                    *common,
                    "--batch-size",
                    str(chosen["batch_size"]),
                    "--learning-guard",
                    "--guard-probe-steps",
                    "32",
                    "--learning-rate",
                    str(peak),
                ]
            )
            if previous is not None:
                checkpoint = previous / "checkpoint_last.pt"
                if not checkpoint.exists():
                    raise RuntimeError(
                        "Failed campaign has no valid checkpoint to resume"
                    )
                arguments.extend(["--resume", str(checkpoint)])
            training = self.child(label, arguments)
            review = read(training / "learning-review.json")
            status = read(training / "progress.json")
            if review.get("action") == "stop":
                if (
                    review.get("reason")
                    in {"gross divergence", "no measurable learning"}
                    and not learning_repaired
                ):
                    repaired = self.repair_learning(common, chosen, training, review)
                    if repaired is not None:
                        peak, previous, learning_repaired = repaired, None, True
                        continue
                self.investigate(training, review)
                return
            if status.get("state") == "complete":
                self.update(
                    state="complete",
                    stage="50 epochs completed",
                    training_output=str(training),
                )
                return
            error = str(status.get("error", ""))
            if "nonfinite" in error.lower() and not learning_repaired:
                repaired = self.repair_learning(
                    common, chosen, training, dict(reason=error)
                )
                if repaired is not None:
                    peak, previous, learning_repaired = repaired, None, True
                    continue
            if any(
                term in error.lower()
                for term in (
                    "invalid v6",
                    "changed sealed",
                    "contract",
                    "digest",
                    "nonfinite",
                )
            ):
                self.investigate(training, dict(action="stop", reason=error))
                return
            self.update(
                stage="resuming failed training from durable checkpoint",
                failed_attempt=str(training),
                error=error,
            )
            previous = training
        raise RuntimeError("Training exhausted two bounded checkpoint retries")

    def repair_learning(self, common, chosen, training, review):
        """Test one bounded optimizer remedy before restarting an unhealthy campaign.

        High learning rate is a hypothesis, never a diagnosed fact from a flat
        loss alone. Two 128-update trials use identical initialization, data,
        masks and untrained validation probe. Restart at a lower rate only if
        that trial demonstrates at least 0.5% probe improvement with finite
        losses. Architecture and the immutable train/validation split stay fixed.
        This is at most one repair round, not an open-ended parameter search.
        """
        from research.bar_gpt.v5.run_pretrain import campaign_arguments

        evidence = []
        for rate in (3e-4, 1e-4):
            output = self.child(
                f"recovery-lr-{rate}",
                campaign_arguments(
                    [
                        *common,
                        "--batch-size",
                        str(chosen["batch_size"]),
                        "--epochs",
                        "1",
                        "--train-steps",
                        "128",
                        "--validation-steps",
                        "32",
                        "--learning-rate",
                        str(rate),
                        "--warmup-updates",
                        "16",
                        "--metrics-every",
                        "32",
                        "--learning-guard",
                        "--guard-probe-steps",
                        "32",
                    ]
                ),
            )
            baseline = read(output / "learning-baseline.json").get("loss")
            history = read(output / "results.json").get("history", [])
            score = history[-1].get("probe", {}).get("loss") if history else None
            if baseline is not None and score is not None and math.isfinite(score):
                improvement = (baseline - score) / max(abs(baseline), 1e-12)
                evidence.append(
                    dict(
                        rate=rate,
                        baseline=baseline,
                        probe=score,
                        improvement=improvement,
                        output=str(output),
                    )
                )
        qualified = [item for item in evidence if item["improvement"] >= 0.005]
        selected = min(qualified, key=lambda item: item["probe"]) if qualified else None
        write_json(
            self.root / "optimizer-repair.json",
            dict(
                triggering_run=str(training),
                trigger=review,
                hypothesis="peak learning rate instability",
                trials=evidence,
                selected=selected,
                decision=(
                    "fresh full campaign with measured lower learning rate"
                    if selected
                    else "no evidenced repair; stop"
                ),
            ),
        )
        return selected["rate"] if selected else None

    def investigate(self, training, review):
        """Preserve concrete diagnostics; never label an unknown failure as repaired.

        Persistent overfit is a legitimate early stop. Unknown learning/data
        failures stop with evidence rather than cycling through new architectures
        or lowering the data-quality requirements. Model revisions, if needed,
        must be implemented in a separately reviewed new encoder module.
        """
        result = read(training / "results.json")
        incident = dict(
            review=review,
            training=str(training),
            history=result.get("history"),
            best_checkpoint=str(training / "checkpoint_best.pt"),
            last_checkpoint=str(training / "checkpoint_last.pt"),
        )
        write_json(self.root / "incident.json", incident)
        self.update(
            state=(
                "early_stopped"
                if review.get("reason") == "persistent overfit"
                else "needs_investigation"
            ),
            stage=review.get("reason"),
            incident=str(self.root / "incident.json"),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    runtime = Path("D:/TradingML/runtimes").resolve()
    args.output, args.dataset = args.output.resolve(), args.dataset.resolve()
    if (
        not runtime.is_dir()
        or not args.output.is_relative_to(runtime)
        or not args.dataset.is_relative_to(runtime)
    ):
        raise ValueError("Use the available workstation runtime authority")
    args.output.mkdir(parents=True, exist_ok=True)
    import msvcrt

    with (args.output / "controller.lock").open("a+b") as owner:
        owner.write(b"0")
        owner.flush()
        owner.seek(0)
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        controller = Controller(args)
        try:
            controller.run()
        except BaseException as error:
            controller.update(
                state="stopped" if isinstance(error, InterruptedError) else "failed",
                stage="controller stopped",
                error=f"{type(error).__name__}: {error}",
            )
            raise


if __name__ == "__main__":
    raise SystemExit(main())
