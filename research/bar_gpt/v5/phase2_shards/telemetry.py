"""Bounded asynchronous generation telemetry; no per-file network work."""

import json
import os
from pathlib import Path
import threading
import time
import uuid

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.wandb_utils import init_wandb
from .contract import write_json


class Telemetry:
    """Upload a coalesced progress snapshot every 30s and preserve local logs.

    The writer has one persistent file handle, one background thread and at most
    one pending snapshot. W&B's own service handles transport. Unit receipts and
    the SQLite ledger, rather than sampled telemetry, remain data authorities.
    """

    def __init__(
        self, root, configuration, mode="online", project="bargpt-phase2-shards-v1"
    ):
        self.root = Path(root)
        load_env_files(
            discover_env_files(Path(__file__).resolve().parents[4]), verbose=False
        )
        identity = self.root / "wandb.json"
        prior = json.loads(identity.read_text()) if identity.exists() else {}
        self.id = prior.get("id", uuid.uuid4().hex[:8])
        self.run = init_wandb(
            entity=os.environ.get("WANDB_ENTITY", ""),
            project=project,
            run_name=self.root.name,
            config=configuration,
            run_dir=self.root,
            mode=mode,
            timeout_seconds=30,
            run_id=self.id,
            resume_mode="allow",
            capture_console=False,
        )
        self.url = getattr(self.run, "url", None)
        self.mode, self.project = mode, project
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.pending, self.error = None, None
        self.journal = (self.root / "generation-metrics.jsonl").open(
            "a", encoding="utf-8", buffering=1
        )
        write_json(identity, dict(id=self.id, url=self.url, project=project, mode=mode))
        if self.run:
            self.run.save(
                str(self.root / "generation-metrics.jsonl"),
                base_path=str(self.root),
                policy="live",
            )
        self.thread = threading.Thread(
            target=self._loop, name="phase2-wandb", daemon=True
        )
        self.thread.start()

    def update(self, snapshot):
        with self.lock:
            self.pending = snapshot

    def _upload(self):
        with self.lock:
            snapshot, self.pending = self.pending, None
        if snapshot is None:
            return
        metrics = {
            "generation/" + key: snapshot.get(key, 0)
            for key in (
                "bytes",
                "units",
                "skipped_streams",
                "bytes_per_second",
                "failed",
                "free_memory_gib",
                "free_disk_gib",
                "active",
                "saving",
                "target_bytes",
            )
        }
        metrics["generation/state"] = snapshot["state"]
        for row in snapshot.get("blocks", []):
            key = f"coverage/split{row['split']}/liquid{row['liquid']}"
            metrics[key + "/selected"] = row["selected"]
            metrics[key + "/deferred"] = row["total"] - row["selected"]
        self.journal.write(
            json.dumps(dict(time=time.time(), **metrics), allow_nan=False) + "\n"
        )
        if self.run:
            self.run.log(metrics)
        self.error = None

    def _loop(self):
        while not self.stop.is_set():
            try:
                self._upload()
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"[:1000]
                write_json(
                    self.root / "wandb-upload-error.json",
                    dict(error=self.error, time=time.time()),
                )
            self.stop.wait(30)

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            raise TimeoutError("W&B generation logger did not drain")
        self._upload()
        self.journal.close()
        if self.run:
            # Small logs/proofs only: never upload multi-TB payloads or secrets.
            for name in ("execution.json", "audit.json", "generation-metrics.jsonl"):
                if (self.root / name).exists():
                    self.run.save(
                        str(self.root / name), base_path=str(self.root), policy="now"
                    )
            self.run.finish()
