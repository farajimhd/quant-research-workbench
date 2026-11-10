"""Exercise real checkpoint orchestration with bounded CPU models and fake loaders.

The training kernels/market loader have separate tests. These cases exercise
normal training, sealed migration, baseline interruption/resume and state retention
without consuming the workstation GPU or reading the live training corpus.
"""

from types import SimpleNamespace
import json

import pytest
import torch

from . import campaign_v1, run_v1
from .data_transition_v1 import model_hash
from .encoder_v1 import EncoderConfig
from .quote_handoff_v1 import prepare_checkpoint_state, rebind_calibration
from .shards.contract import digest
from .test_quote_handoff_v1 import calibration_fixture


class TinyModel(torch.nn.Module):
    """Minimal real parameters, retaining the same config serialization API."""

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.weight = torch.nn.Parameter(torch.randn(2, 2))

    def cuda(self):
        return self


class Reporter:
    """Retain step logs and optionally interrupt only at a durable boundary."""

    def __init__(self, output, stop_key=None):
        self.output, self.stop_key, self.rows = output, stop_key, []

    def update(self, **_):
        pass

    def log(self, values, step):
        self.rows.append((values, step))
        if self.stop_key in values:
            (self.output / "STOP").write_text("test boundary")


def test_legacy_masked_history_upgrade_resumes_validation_without_updates(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(campaign_v1, "MaskedAutoencoderV1", TinyModel)
    monkeypatch.setattr(torch.cuda, "get_rng_state_all", lambda: [])
    monkeypatch.setattr(torch.cuda, "set_rng_state_all", lambda _: None)
    monkeypatch.setattr(campaign_v1, "close_loader", lambda _: None)
    monkeypatch.setitem(run_v1.CONFIGURATIONS, "tiny", EncoderConfig())

    class Loader:
        dataset = SimpleNamespace(counters=torch.zeros(1, 3))

        def __len__(self):
            return 1

    monkeypatch.setattr(run_v1, "loader_for", lambda *_, **__: Loader())
    calls = []

    def steps(model, optimizer, loader, **kw):
        calls.append((kw["training"], kw["metric_prefix"], model_hash(model)))
        if kw["training"]:
            kw["before_step"](0)
            kw["on_step"](0)
        return dict(
            loss=0.3 if kw["training"] else 0.6,
            exhausted=True,
            consumed_batches=1,
            samples_per_second=10,
        )

    monkeypatch.setattr(campaign_v1, "run_steps", steps)
    args = SimpleNamespace(
        seed=17,
        model="tiny",
        attention="sdpa",
        attention_dropout=0.0,
        learning_rate=0.001,
        min_learning_rate=0.00005,
        epochs=2,
        warmup_updates=1000,
        batch_size=2,
        workers=1,
        precision="bf16",
        train_steps=0,
        validation_steps=0,
        learning_guard=True,
        guard_probe_steps=1,
        overfit_patience=8,
        prefetch=2,
        metrics_every=1000,
        loss_every=10,
        resume=None,
        output=tmp_path / "old",
    )
    args.output.mkdir()
    stats = SimpleNamespace(median=torch.zeros(6), scale=torch.ones(6))
    campaign_v1.train_campaign(
        args,
        dict(manifest_hash="split"),
        stats,
        Reporter(args.output, "epoch/validation_full_loss"),
    )
    legacy = torch.load(args.output / "checkpoint_last.pt", weights_only=False)
    legacy["contract"].pop("validation_protocol")
    legacy.update(phase="validation", step=2)
    legacy["scheduler"]["updates"] = 2
    torch.save(legacy, args.output / "checkpoint_last.pt")
    best = torch.load(args.output / "checkpoint_best.pt", weights_only=False)
    best["contract"].pop("validation_protocol")
    torch.save(best, args.output / "checkpoint_best.pt")
    args.resume = args.output / "checkpoint_last.pt"
    args.output = tmp_path / "new"
    args.output.mkdir()
    calls.clear()
    campaign_v1.train_campaign(
        args, dict(manifest_hash="split"), stats, Reporter(args.output)
    )
    state = torch.load(args.output / "checkpoint_last.pt", weights_only=False)
    assert not any(t for t, _, _ in calls)
    assert [role for _, role, _ in calls] == [
        "validation_full_untrained_probe",
        "validation_full",
        "validation_full_probe",
    ]
    assert state["step"] == state["scheduler"]["updates"] == 2
    assert state["masked_validation_history"]["history"] == legacy["history"]
    assert (
        state["masked_validation_history"]["probe_baseline"] == legacy["probe_baseline"]
    )
    assert state["contract"]["validation_protocol"] == "all-visible-v1"
    assert [r["epoch"] for r in state["history"]] == [2]
    assert (args.output / "masked-validation/checkpoint_best.pt").exists()
    for name, value in state["model"].items():
        assert torch.equal(value, legacy["model"][name])


@pytest.mark.parametrize("interrupt_baseline", [False, True])
@pytest.mark.parametrize("reset_schedule", [False, True])
def test_corrected_campaign_preserves_state_and_resumes_baseline_best(
    tmp_path, monkeypatch, interrupt_baseline, reset_schedule
):
    """No update occurs before baselines; no-history best can survive another resume."""
    monkeypatch.setattr(campaign_v1, "MaskedAutoencoderV1", TinyModel)
    monkeypatch.setattr(torch.cuda, "get_rng_state_all", lambda: [])
    monkeypatch.setattr(torch.cuda, "set_rng_state_all", lambda _: None)
    monkeypatch.setattr(campaign_v1, "close_loader", lambda _: None)
    monkeypatch.setitem(run_v1.CONFIGURATIONS, "tiny", EncoderConfig())

    class Loader:
        dataset = SimpleNamespace(counters=torch.zeros(1, 3))

        def __len__(self):
            return 1

    monkeypatch.setattr(run_v1, "loader_for", lambda *_, **__: Loader())
    calls = []

    def run_steps(model, optimizer, loader, **options):
        role = options["metric_prefix"]
        calls.append((options["training"], role, model_hash(model)))
        if options["training"]:
            options["before_step"](0)
            options["on_step"](0)
        return dict(
            loss=(0.3 if options["training"] else 0.6),
            exhausted=True,
            consumed_batches=1,
            samples_per_second=10,
        )

    monkeypatch.setattr(campaign_v1, "run_steps", run_steps)
    args = SimpleNamespace(
        seed=17,
        model="tiny",
        attention="sdpa",
        attention_dropout=0.0,
        learning_rate=1e-3,
        min_learning_rate=5e-5,
        epochs=2,
        warmup_updates=1000,
        batch_size=2,
        workers=1,
        precision="bf16",
        train_steps=0,
        validation_steps=0,
        learning_guard=True,
        guard_probe_steps=1,
        overfit_patience=8,
        prefetch=2,
        metrics_every=1000,
        loss_every=10,
        resume=None,
        output=tmp_path / "old",
    )
    args.output.mkdir()
    old, certificate = calibration_fixture()
    stats = SimpleNamespace(
        median=torch.tensor(old["median"]), scale=torch.tensor(old["scale"])
    )
    # Normal path produces an actual complete-epoch checkpoint before migration.
    campaign_v1.train_campaign(
        args,
        {"manifest_hash": "old"},
        stats,
        Reporter(args.output, "epoch/validation_full_loss"),
    )
    parent_path = args.output / "checkpoint_last.pt"
    parent = torch.load(parent_path, weights_only=False)
    assert parent["epoch"] == 1 and parent["scheduler"]["updates"] == 1
    manifest = dict(
        dataset="new",
        counts=dict(train=8, validation=2),
        cutoff_us=100,
        purged_crossing=2,
    )
    manifest["manifest_hash"] = digest(manifest)
    certificate.update(
        target_split=manifest["manifest_hash"],
        membership_hash="b" * 64,
        counts=manifest["counts"],
        cutoff_us=100,
        purged_crossing=2,
    )
    certificate["hash"] = digest({k: v for k, v in certificate.items() if k != "hash"})
    calibration = rebind_calibration(old, certificate, "old", manifest["manifest_hash"])
    migrated = prepare_checkpoint_state(parent, certificate, old, calibration, "a" * 64)
    if reset_schedule:
        from .schedule_transition_v1 import prepare

        prepare(parent, migrated)
        args.epochs, args.learning_rate, args.warmup_updates = 41, 3e-4, 0
    prepared_path = tmp_path / "prepared.pt"
    torch.save(migrated, prepared_path)
    args.resume, args.output = prepared_path, tmp_path / "new"
    args.output.mkdir()
    (args.output / "calibration.json").write_text(json.dumps(calibration))
    calls.clear()
    reporter = Reporter(
        args.output,
        (
            "data_revision/certificate"
            if interrupt_baseline
            else ("epoch/validation_full_loss" if reset_schedule else None)
        ),
    )
    campaign_v1.train_campaign(args, manifest, stats, reporter)
    assert [(training, role) for training, role, _ in calls[:3]] == [
        (False, "data_revision_untrained_probe"),
        (False, "data_revision_trained_probe"),
        (False, "data_revision_trained_validation"),
    ]
    state = torch.load(args.output / "checkpoint_last.pt", weights_only=False)
    assert state["data_transition"]["state"] == "baselines-complete"
    assert (
        state["data_transition"]["historical_evidence"]["history"] == parent["history"]
    )
    assert calls[1][2] == calls[2][2]
    if interrupt_baseline:
        assert state["history"] == [] and state["best_epoch"] == 1
        assert state["step"] == parent["step"]
        assert state["scheduler"] == migrated["scheduler"]
        assert torch.equal(state["rng"], parent["rng"])
        args.resume, args.output = (
            args.output / "checkpoint_last.pt",
            tmp_path / "again",
        )
        args.output.mkdir()
        (args.output / "calibration.json").write_text(json.dumps(calibration))
        calls.clear()
        campaign_v1.train_campaign(
            args,
            manifest,
            stats,
            Reporter(
                args.output, "epoch/validation_full_loss" if reset_schedule else None
            ),
        )
        # Sealed baseline is reused, and baseline best is copied despite empty history.
        assert calls[0][:2] == (True, None)
        state = torch.load(args.output / "checkpoint_last.pt", weights_only=False)
    assert state["epoch"] == 2 and state["step"] == parent["step"] + 1
    assert state["scheduler"]["updates"] == (
        1 if reset_schedule else parent["scheduler"]["updates"] + 1
    )
    if reset_schedule:
        assert state["scheduler"]["epoch"] == 0
        assert state["history"][0]["learning_rate"] == pytest.approx(3e-4)
    assert [row["epoch"] for row in state["history"]] == [2]
    assert state["best_epoch"] == 1  # Equal score retains the trained baseline best.
    for name, tensor in state["model"].items():
        assert torch.equal(tensor, parent["model"][name])
    assert state["optimizer"]["state"] == parent["optimizer"]["state"]
    assert state["contract"]["model"] == parent["contract"]["model"]
