"""Test measurable metrics, exact resume, and terminal states on a tiny CPU job."""

import io
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from rich.console import Console

from . import train as engine
from .metrics import Metrics
from .model import Autoencoder
from .panel import render
from .schedule import learning_rate
from .test_model import example, parameters
from .transforms import coordinates
from ..v5.shards.phase1_contract import FRAMES
from ..phase2_v2.train import load_checkpoint
from ..v5.checkpoint_transition_v1 import state_hash
from ..phase1_v26.reporting import ReportingFailure


def fixture_batch():
    """A whole 64-bar window; activity rises, candles have known direction."""
    raw, values = example(torch.float32)
    values = values.repeat(64, 1).unsqueeze(0)
    raw = raw.repeat(64, 1).unsqueeze(0).double()
    for index in (4, 5, 10, 11, 16, 17):
        raw[..., index] = torch.arange(64) + 1
        values[..., index] = raw[..., index].log1p()
    valid = torch.ones_like(values, dtype=torch.bool)
    batch = SimpleNamespace(raw=raw, target=raw, frames=torch.tensor([FRAMES[1]]))
    batch.to = lambda *args, **kwargs: batch
    return batch, values, valid


def test_metrics_perfect_and_wrong_activity_direction():
    batch, values, valid = fixture_batch()
    model = Autoencoder(parameters())
    code, _ = coordinates(values[..., :18], valid[..., :18])
    out = dict(logs=values[..., :18], code=code, normalized=code)
    metric = Metrics()
    metric.add(model, out, values, valid, batch)
    result = metric.result()
    assert result["nonfinite"] == 0
    directions = [v for k, v in result["metrics"].items() if k.endswith("/balanced_accuracy_pct")]
    assert directions and all(v == 100 for v in directions)
    out["logs"] = out["logs"].clone()
    out["logs"][..., 4] = torch.flip(out["logs"][..., 4], [1])
    wrong = Metrics()
    wrong.add(model, out, values, valid, batch)
    scores = wrong.result()["metrics"]
    assert any(v == 0 for k, v in scores.items() if "trade_volume/balanced_accuracy_pct" in k)


def test_schedule_endpoints_and_invalid_progress():
    assert learning_rate(0, 100) == pytest.approx(1e-4)
    assert learning_rate(100, 100) == pytest.approx(1e-6)
    assert learning_rate(50, 100) == pytest.approx((1e-4 + 1e-6) / 2)
    with pytest.raises(ValueError):
        learning_rate(101, 100)


class Recorder:
    """Test reporter captures actual training calls without starting a W&B job."""

    def __init__(self):
        self.metrics = []
        self.state = {}

    def log(self, values, step):
        self.metrics.append((step, values))

    def update(self, **values):
        self.state.update(values)


def test_exact_resume_and_full_validation(tmp_path, monkeypatch):
    torch.set_num_threads(2)
    batch, values, valid = fixture_batch()
    monkeypatch.setattr(
        engine,
        "loader",
        lambda manifest, args, split, epoch=0: [batch] * (3 if split == "train" else 2),
    )
    monkeypatch.setattr(engine, "close_loader", lambda source: None)
    monkeypatch.setattr(engine, "prepare_raw", lambda rows, native: batch)
    monkeypatch.setattr(
        engine, "forward_batch", lambda model, batch: (model(values, valid), values, valid)
    )
    np.save(tmp_path / "development.npy", np.zeros(1))
    args = SimpleNamespace(
        output=tmp_path / "whole",
        data=tmp_path,
        device="cpu",
        epochs=2,
        learning_rate=1e-4,
        min_learning_rate=1e-6,
        resume=None,
        checkpoint_every=2,
        validation_every=2,
        log_every=1,
        validation_batch_size=1,
        max_updates=0,
    )
    args.output.mkdir()
    manifest = {"counts": {"train": 3, "validation": 2}}
    contract = {"test": "exact deterministic resume"}
    torch.manual_seed(7)
    whole = Autoencoder(parameters())
    assert engine.train(whole, manifest, args, contract, Recorder()) == "complete"
    whole_state = load_checkpoint(args.output / "checkpoint_last.pt", contract)
    args.output = tmp_path / "resumed"
    args.output.mkdir()
    args.max_updates = 2
    torch.manual_seed(7)
    partial = Autoencoder(parameters())
    assert engine.train(partial, manifest, args, contract, Recorder()) == "bounded_complete"
    args.max_updates = 0
    args.resume = args.output / "checkpoint_last.pt"
    resumed = Autoencoder(parameters())
    assert engine.train(resumed, manifest, args, contract, Recorder()) == "complete"
    resumed_state = load_checkpoint(args.resume, contract)
    assert whole_state["windows"] == resumed_state["windows"] == 6
    assert whole_state["scheduler"] == resumed_state["scheduler"]
    for key, tensor in whole_state["model"].items():
        torch.testing.assert_close(tensor, resumed_state["model"][key], rtol=0, atol=0)
    assert state_hash(whole_state["optimizer"]) == state_hash(resumed_state["optimizer"])
    assert state_hash(whole_state["rng"]) == state_hash(resumed_state["rng"])
    assert (args.output / "validation-002.json").is_file()


def test_interrupted_validation_is_not_complete(tmp_path, monkeypatch):
    batch, values, valid = fixture_batch()
    monkeypatch.setattr(engine, "prepare_raw", lambda rows, native: batch)
    monkeypatch.setattr(
        engine, "forward_batch", lambda model, batch: (model(values, valid), values, valid)
    )
    args = SimpleNamespace(output=tmp_path, device="cpu", validation_batch_size=1, log_every=1)
    (tmp_path / "STOP").write_text("Human stop; preserve")
    recorder = Recorder()
    result = engine.evaluate(Autoencoder(parameters()), {}, args, recorder, 0, np.zeros(2))
    assert result is None and recorder.state["validation_complete"] is False
    assert not (tmp_path / "development-latest.json").exists()
    assert (tmp_path / "STOP").read_text() == "Human stop; preserve"


def test_reporting_failure_preserves_committed_update(tmp_path, monkeypatch):
    batch, values, valid = fixture_batch()
    monkeypatch.setattr(engine, "loader", lambda *args: [batch] * 3)
    monkeypatch.setattr(engine, "close_loader", lambda source: None)
    monkeypatch.setattr(engine, "prepare_raw", lambda rows, native: batch)
    monkeypatch.setattr(
        engine, "forward_batch", lambda model, batch: (model(values, valid), values, valid)
    )
    np.save(tmp_path / "development.npy", np.zeros(1))
    args = SimpleNamespace(
        output=tmp_path,
        data=tmp_path,
        device="cpu",
        epochs=1,
        learning_rate=1e-4,
        min_learning_rate=1e-6,
        resume=None,
        checkpoint_every=2,
        validation_every=2,
        log_every=1,
        validation_batch_size=1,
        max_updates=0,
    )

    class FailedUpload(Recorder):
        def log(self, values, step):
            if "train/loss" in values:
                raise ReportingFailure("Injected upload failure")
            super().log(values, step)

    contract = {"test": "reporting recovery"}
    with pytest.raises(ReportingFailure):
        engine.train(
            Autoencoder(parameters()), {"counts": {"train": 3}}, args, contract, FailedUpload()
        )
    state = load_checkpoint(tmp_path / "checkpoint_last.pt", contract)
    assert state["step"] == state["windows"] == state["cursor"][1] == 1


@pytest.mark.parametrize("width", [60, 100])
@pytest.mark.parametrize("state", ["running", "failed", "complete", "stopped"])
def test_panel_readable_without_ansi(width, state):
    output = io.StringIO()
    console = Console(file=output, width=width, force_terminal=False)
    console.print(
        render(
            dict(
                state=state,
                stage="training",
                updated=100,
                epoch=1,
                epochs=20,
                epoch_done=1,
                epoch_total=100,
                error="Checkpoint preserved" if state == "failed" else None,
            ),
            now=200,
        )
    )
    text = output.getvalue()
    assert "\x1b" not in text and state.upper() in text
    if state == "running":
        assert "STALE" in text
    assert max(map(len, text.splitlines())) <= width


def test_panel_rejects_inconsistent_completion_label():
    output = io.StringIO()
    Console(file=output, width=100).print(
        render(
            dict(
                state="complete", validation_complete=True, validation_windows=1, validation_total=2
            )
        )
    )
    assert "COVERAGE MISMATCH" in output.getvalue()
    assert "Validation (partial)" in output.getvalue()
