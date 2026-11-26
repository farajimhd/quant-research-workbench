"""Profile verified migration sessions and launch one full run after certification."""
import argparse
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import polars as pl

from ..v5.shards.contract import digest, file_hash, write_json
from .migrate_unique import FIELDS
from .unique_data import load_unique_release


def profile_sample(migration, destination):
    """Link verified journal payloads into a catalog usable only by profiling."""
    binding = json.loads((migration / "migration.json").read_text())
    database = migration / "migration.sqlite"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        recorded = [json.loads(row) for (row,) in db.execute("SELECT result FROM sessions ORDER BY key")]
    rng = random.Random(52379)
    selected = []
    for split, budget in ((0, 512), (1, 48)):
        rows = [row for row in recorded if row[2] == split and row[5] > 0]
        # Select clusters spanning the observed inventory. Keeping neighbors
        # allows an actual comparison of the cross-session cache.
        clusters = [rows[start:start+16] for start in range(0, len(rows)-15, 16)]
        selected.extend(row for group in rng.sample(clusters, budget//16) for row in group)
    for row in selected:
        target = destination / row[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(migration / row[0], target)
    catalog = destination / "sessions.parquet"
    rows = pl.DataFrame(selected, schema=list(FIELDS), orient="row")
    rows.write_parquet(catalog)
    manifest = dict(binding, profile_only=True, sessions=rows.height,
                    unique_origins=rows["candidates"].sum(), catalog_sha256=file_hash(catalog),
                    split_counts=rows.group_by("split").agg(pl.sum("candidates", "eligible"),
                                                           pl.len().alias("sessions")).sort("split").to_dicts())
    certificate = destination / "certificate.json"
    write_json(certificate, dict(manifest, hash=digest(manifest)))
    return certificate


def wait_for_release(migration, control, expected_parent):
    deadline = time.monotonic() + 12 * 3600
    certificate = migration / "certificate.json"
    while time.monotonic() < deadline:
        status = json.loads((migration / "progress.json").read_text())
        if (control / "STOP").exists() or (control.parent / "STOP").exists():
            raise RuntimeError("Full-run launch was stopped")
        if status["state"] == "complete":
            release, manifest = load_unique_release(certificate)
            if (manifest.get("profile_only") or manifest["parent"]["hash"] != expected_parent
                    or len(release) != status["total"] or len(release) != status["completed"]
                    or manifest["unique_origins"] != status["unique"]
                    or manifest["source_occurrences"] != status["occurrences"]):
                raise ValueError("Release certificate disagrees with the completed migration")
            return certificate, manifest
        if status["state"] != "running" or time.time() - status["updated"] > 900:
            raise RuntimeError(f"Migration did not finish: {status}")
        time.sleep(15)
    raise TimeoutError("Migration has not sealed after twelve hours")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("migration", "control", "output", "teacher", "calibration", "checkpoint"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    base = Path("D:/TradingML/runtimes/bar_gpt").resolve()
    if (os.environ.get("COMPUTERNAME", "").upper() != "DESKTOP-SAAI85T"
            or any(path.resolve().parent != base for path in (args.control, args.output, args.migration))
            or args.output.exists()):
        raise ValueError("The workstation requires a new training output and its own control directory")
    args.control.mkdir()
    status = args.control / "launch-status.json"
    proof = json.loads(args.checkpoint.with_suffix(".proof.json").read_text())
    parent = json.loads((args.checkpoint.parent / "manifest.json").read_text())["execution_model"]["data"]["hash"]
    shared = [sys.executable, "-B", "-u", "-m", "research.bar_gpt.phase2_v5.run_train",
              "--source-revision", args.source_revision, "--teacher", str(args.teacher),
              "--calibration", str(args.calibration), "--from-completed-pass", str(args.checkpoint),
              "--batch-size", "8192", "--token-batch", "8192", "--graph-updates", "--locality", "8"]
    try:
        write_json(status, dict(state="profiling", parent_step=proof["step"]))
        sample = profile_sample(args.migration, args.control / "sample")
        results = []
        for tag, workers, lanes, reuse in (("baseline", 8, 2, False), ("cache", 8, 2, True),
                                           ("loader", 16, 2, True), ("streams", 16, 4, True)):
            directory = base / f"{args.output.name}-profile-{tag}"
            command = shared + ["--output", str(directory), "--unique-release", str(sample),
                                "--workers", str(workers), "--session-lanes", str(lanes),
                                "--profile-sessions", "512", "--eval-sessions", "48"]
            if not reuse:
                command += ["--no-cross-session-cache"]
            subprocess.run(command, check=True)
            report = json.loads((directory / "profile.json").read_text())
            results.append(dict(tag=tag, workers=workers, lanes=lanes, reuse=reuse, **report))
        baseline = results[0]
        for trial in results[1:]:
            if any(trial[k] != baseline[k] for k in ("updates", "interval_origins", "validation_origins")):
                raise ValueError("Profile settings changed the workload")
            if abs(trial["validation_loss"] - baseline["validation_loss"]) > 1e-5:
                raise ValueError("Profile settings changed forecast quality on the matched workload")
        # Account for both training and validation work on the same sample.
        selected = min(results, key=lambda row: row["seconds"] + row["validation_seconds"])
        write_json(args.control / "profiles.json", dict(selected=selected, trials=results))
        write_json(status, dict(state="waiting for verified release", parent_step=proof["step"], selection=selected["tag"]))
        certificate, release = wait_for_release(args.migration, args.control, parent)
        if file_hash(args.checkpoint) != proof["sha256"] or (args.output / "STOP").exists():
            raise ValueError("Parent checkpoint changed or training was stopped before launch")
        command = shared + ["--output", str(args.output), "--unique-release", str(certificate),
                            "--workers", str(selected["workers"]), "--session-lanes", str(selected["lanes"]),
                            "--validation-every", "5000"]
        if not selected["reuse"]:
            command += ["--no-cross-session-cache"]
        write_json(status, dict(state="starting training", command=command, selection=selected["tag"],
                                release=release["hash"], split_counts=release["split_counts"]))
        with (args.control / "training.log").open("a", encoding="utf-8") as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
        final = json.loads((args.output / "progress.json").read_text())
        write_json(status, dict(state=final["state"], training_progress=str(args.output / "progress.json")))
    except BaseException as error:
        write_json(status, dict(state="failed", error=repr(error)))
        raise


if __name__ == "__main__":
    main()
