"""Resume identity and journal consolidation tests need neither CUDA nor a network."""

import json
import sys
from types import SimpleNamespace

import pytest

from research.bar_gpt.v5 import wandb_relay_v1 as relay
from research.bar_gpt.v5 import reporting_v1
from research.bar_gpt.v5.wandb_identity_v1 import (
    WriterLease,
    persist,
    resume_identity,
    signature,
)


def attempt(root, config, run_id):
    """Create a legacy attempt fixture, retaining its checkpoint provenance."""
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(config))
    (root / "progress.json").write_text(
        json.dumps(
            dict(
                state="stopped",
                wandb_url=f"https://wandb.ai/team/project/runs/{run_id}",
            )
        )
    )
    return root


def test_resume_uses_original_identity_and_hyperparameters_fork(tmp_path):
    base = dict(model="large", learning_rate=0.001, workers=32, resume=None)
    first = attempt(tmp_path / "train-01", base, "original")
    second_config = dict(base, resume=str(first / "last.pt"), workers=8)
    second = attempt(tmp_path / "train-02", second_config, "accidental")
    resumed = dict(base, resume=str(second / "last.pt"), source_revision="new")
    identity = resume_identity(resumed, "project")
    assert identity["id"] == "original" and identity["name"] == "train-01"
    assert resume_identity(dict(resumed, learning_rate=0.002), "project") is None
    assert resume_identity(resumed, "another-project") is None
    # A persisted identity is also used without needing older attempt directories.
    persist(second, identity)
    assert resume_identity(resumed, "project")["id"] == "original"
    assert signature(base) == signature(second_config)


def test_writer_lease_prevents_two_sdk_writers(tmp_path):
    lease = WriterLease(tmp_path).acquire()
    try:
        with pytest.raises(RuntimeError, match="Another SDK writer"):
            WriterLease(tmp_path).acquire()
    finally:
        lease.close()
    WriterLease(tmp_path).acquire().close()


def test_reporter_passes_original_id_and_name_to_sdk(tmp_path, monkeypatch):
    config = dict(model="large", learning_rate=0.001)
    first = attempt(tmp_path / "train-01", config, "original")
    root = tmp_path / "train-04"
    root.mkdir()
    resumed = dict(config, resume=str(first / "last.pt"))
    calls = []
    run = SimpleNamespace(
        config=SimpleNamespace(update=lambda *a, **k: None),
        url="https://wandb.ai/team/project/runs/original",
        define_metric=lambda *a, **k: None,
        save=lambda *a, **k: None,
    )

    def start(**kwargs):
        calls.append(kwargs)
        return run

    monkeypatch.setattr(reporting_v1, "init_wandb", start)
    monkeypatch.setattr(reporting_v1.Reporter, "_observe", lambda self: None)
    reporter = reporting_v1.Reporter(root, resumed, project="project")
    try:
        assert calls[0]["run_id"] == "original"
        assert calls[0]["run_name"] == "train-01"
        assert calls[0]["resume_mode"] == "must"
        assert resumed["wandb_identity"]["id"] == "original"
    finally:
        reporter.journal.close()
        reporter.writer_lease.close()


def test_relay_replays_only_missing_complete_payloads(tmp_path, monkeypatch):
    config = dict(model="large", learning_rate=0.001)
    first = attempt(tmp_path / "train-01", config, "original")
    second = attempt(
        tmp_path / "train-02", dict(config, resume=str(first / "last.pt")), "other"
    )
    existing = dict(step=10, **{"train/loss": 1.0})
    missing = dict(step=20, **{"train/loss": 0.8})
    acknowledged = dict(step=30, **{"train/loss": 0.7})
    (first / "metrics.jsonl").write_text(json.dumps(existing) + "\n")
    (second / "metrics.jsonl").write_text(
        "\n".join(map(json.dumps, [existing, missing, missing, acknowledged]))
        + '\n{"step":40'
    )
    rows, uploads, init = [], [], []
    run = SimpleNamespace(
        define_metric=lambda *a, **k: None,
        log=rows.append,
        save=lambda path, **k: uploads.append(path),
        finish=lambda **k: None,
    )

    def start(**kwargs):
        init.append(kwargs)
        return run

    remote = SimpleNamespace(
        scan_history=lambda **k: [
            {"migration/event_id": relay.fingerprint(acknowledged)}
        ]
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb",
        SimpleNamespace(Api=lambda **k: SimpleNamespace(run=lambda path: remote)),
    )
    monkeypatch.setattr(relay, "init_wandb", start)
    monkeypatch.setattr(relay, "load_env_files", lambda *a, **k: None)
    output = tmp_path / "relay"
    assert (
        relay.main(["--sources", str(first), str(second), "--output", str(output)]) == 0
    )
    assert len(rows) == 1 and rows[0]["global_step"] == 20
    assert init[0]["run_id"] == "original" and init[0]["resume_mode"] == "must"
    assert len(uploads) == 8  # Two snapshots of two files from two attempts.
    assert (output / "complete.json").exists()
    assert json.loads((second / "wandb-identity.json").read_text())["id"] == "original"
