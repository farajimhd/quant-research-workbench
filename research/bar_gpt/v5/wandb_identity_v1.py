"""Keep experiment identity independent of process/output-directory identity.

Inputs are the trainer's JSON-compatible argument manifest and an optional
checkpoint path. No tensors/checkpoints are loaded here. Operational changes
(workers, output paths, source revisions, upload cadence) preserve identity;
model, data selection, seed and optimization changes start a new experiment.
"""

import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from .shards.contract import write_json

# Explicit experiment settings exclude performance-only loader and logging knobs.
EXPERIMENT_KEYS = (
    "mode",
    "model",
    "models",
    "attention",
    "attention_dropout",
    "batch_size",
    "precision",
    "learning_rate",
    "scheduler",
    "min_learning_rate",
    "warmup_updates",
    "epochs",
    "train_steps",
    "validation_steps",
    "seed",
    "mask_rate",
    "dataset",
    "split_manifest",
    "calibration",
)


def signature(config):
    """Hash scientific settings in an argument/manifest dictionary deterministically."""
    values = {key: config.get(key) for key in EXPERIMENT_KEYS}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def read_identity(root):
    """Read a persisted identity, or recover an older run's identity from its URL."""
    path = root / "wandb-identity.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    status = json.loads((root / "progress.json").read_text(encoding="utf-8"))
    url = status.get("wandb_url")
    if not url:
        raise ValueError(f"Cannot recover W&B identity from {root}")
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) != 4 or parts[2] != "runs":
        raise ValueError("Unexpected W&B run URL")
    return dict(
        id=parts[3],
        entity=parts[0],
        project=parts[1],
        name=root.name,
        root=str(root),
        signature=signature(manifest),
        url=url,
    )


def resume_identity(config, project):
    """Return the oldest compatible ancestor; fail visibly on broken provenance.

    A changed experiment setting or project intentionally returns None (new run).
    Existing identity files end traversal, so a deliberately forked experiment
    cannot accidentally attach to its earlier parent.
    """
    checkpoint = config.get("resume")
    expected = signature(config)
    identity, visited = None, set()
    while checkpoint:
        root = Path(checkpoint).parent
        if str(root) in visited:
            raise ValueError("Cyclic checkpoint resume lineage")
        visited.add(str(root))
        candidate = read_identity(root)
        if candidate["signature"] != expected or candidate["project"] != project:
            break
        identity = candidate
        if (root / "wandb-identity.json").exists():
            break
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        checkpoint = manifest.get("resume")
    return identity


class WriterLease:
    """One SDK writer per remote identity; OS locks release even after a crash.

    A resumed trainer asks an active log relay to drain, then waits at most 60s.
    It never stops a trainer or steals another writer's lock.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.file = (self.root / "wandb-writer.lock").open("a+b")
        self.file.write(b"0")
        self.file.flush()
        self.locked = False

    def acquire(self, *, takeover=False):
        """Acquire an exclusive byte lock, optionally requesting relay handoff."""
        deadline = time.monotonic() + (60 if takeover else 0)
        while True:
            try:
                self.file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.locked = True
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.file.close()
                    raise RuntimeError("Another SDK writer owns this W&B run") from None
                # Only the relay watches this marker; a trainer is never stopped.
                (self.root / "wandb-relay.STOP").write_text(
                    "Reporter handoff", encoding="utf-8"
                )
                time.sleep(1)

    def close(self):
        """Release the writer lease after SDK uploads have drained."""
        self.file.close()


def persist(root, identity):
    """Publish a small, credential-free identity beside each attempt's evidence."""
    write_json(Path(root) / "wandb-identity.json", identity)
