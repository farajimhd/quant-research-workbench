"""Consolidate resumed-attempt metric journals into their original W&B run.

This CPU-only sidecar reads complete JSONL records and tails the active attempt;
it never imports torch, reads model weights or controls the trainer. Existing
remote history is preserved. Exact duplicate payloads are skipped across attempt
boundaries; migration event IDs make retries idempotent after remote commits.
Local source journals remain immutable and are uploaded as separate evidence.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.wandb_utils import init_wandb
from .shards.contract import write_json
from .wandb_identity_v1 import WriterLease, persist, read_identity, signature


def fingerprint(row):
    """Hash one scalar metric payload without depending on JSON key order."""
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


def complete_rows(path, offset=0):
    """Yield (end byte offset, JSON row); defer an incomplete concurrent write."""
    with path.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                return
            yield stream.tell(), json.loads(line)


def upload_attempts(run, sources, output):
    """Attach separate, immutable snapshots; never replace the original evidence."""
    for source in sources:
        destination = output / "attempts" / source.name
        destination.mkdir(parents=True, exist_ok=True)
        for name in (
            "metrics.jsonl",
            "manifest.json",
            "results.json",
            "calibration.json",
        ):
            path = source / name
            if path.exists():
                payload = path.read_bytes()
                if name.endswith("jsonl"):
                    payload = payload[: payload.rfind(b"\n") + 1]
                target = destination / name
                target.write_bytes(payload)
                run.save(str(target), base_path=str(output), policy="now")


def main(argv=None):
    """Replay historical attempts and optionally follow the last attempt live.

    --sources must be chronological attempt directories, starting with the
    original run. --output holds only relay logs/status and immutable snapshots.
    The original run's local journal is already uploaded: only missing later
    payloads are appended to its remote charts, using original global steps.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    load_env_files(
        discover_env_files(Path(__file__).resolve().parents[3]), verbose=False
    )
    identity = read_identity(args.sources[0])
    # Validate scientific continuity before publishing shared identity to attempts.
    for source in args.sources:
        config = json.loads((source / "manifest.json").read_text())
        if signature(config) != identity["signature"]:
            raise ValueError(f"Experiment settings differ at {source}")
    lease = WriterLease(identity["root"]).acquire()
    run = None
    try:
        import wandb

        remote = wandb.Api(timeout=30).run(
            f'{identity["entity"]}/{identity["project"]}/{identity["id"]}'
        )
        # A retry reconciles remote acknowledgments, not merely a local cursor.
        acknowledged = {
            row["migration/event_id"]
            for row in remote.scan_history(keys=["migration/event_id"], page_size=1000)
            if row.get("migration/event_id")
        }
        run = init_wandb(
            entity=identity["entity"],
            project=identity["project"],
            run_name=identity["name"],
            config={},
            run_dir=args.output,
            mode="online",
            timeout_seconds=60,
            run_id=identity["id"],
            resume_mode="must",
            capture_console=False,
        )
        if run is None:
            raise RuntimeError("Online metric consolidation requires an active SDK run")
        run.define_metric("global_step")
        run.define_metric("*", step_metric="global_step")
        upload_attempts(run, args.sources, args.output)
        for source in args.sources:
            persist(source, identity)
        # The original journal's payloads are already present in remote history.
        seen = {
            fingerprint(row)
            for _, row in complete_rows(args.sources[0] / "metrics.jsonl")
        }
        offsets = {str(source): 0 for source in args.sources[1:]}
        uploaded = duplicates = 0
        stop = Path(identity["root"]) / "wandb-relay.STOP"
        if stop.exists():
            raise RuntimeError("Existing relay STOP must be reviewed before launching")
        while True:
            for source in args.sources[1:]:
                key = str(source)
                for offset, row in complete_rows(
                    source / "metrics.jsonl", offsets[key]
                ):
                    event = fingerprint(row)
                    if event not in seen and event not in acknowledged:
                        metrics = dict(row)
                        step = metrics.pop("step")
                        run.log(
                            dict(
                                metrics,
                                global_step=step,
                                **{"migration/event_id": event},
                            )
                        )
                        uploaded += 1
                    else:
                        duplicates += 1
                    seen.add(event)
                    offsets[key] = offset
            status = json.loads((args.sources[-1] / "progress.json").read_text())
            following = (
                args.follow and status["state"] == "running" and not stop.exists()
            )
            write_json(
                args.output / "progress.json",
                dict(
                    state="following" if following else "draining",
                    updated=time.time(),
                    url=identity["url"],
                    uploaded=uploaded,
                    duplicates=duplicates,
                    previously_acknowledged=len(acknowledged),
                    offsets=offsets,
                    trainer_epoch=status.get("epoch"),
                    trainer_done=status.get("done"),
                ),
            )
            if not following:
                break
            time.sleep(5)
        upload_attempts(run, args.sources, args.output)
        run.finish()
        run = None
        write_json(
            args.output / "complete.json",
            dict(state="complete", updated=time.time(), url=identity["url"]),
        )
        return 0
    finally:
        if run:
            run.finish(exit_code=1)
        lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
