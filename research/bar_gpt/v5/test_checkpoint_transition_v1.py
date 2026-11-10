"""Independent checkpoint copies must preserve exact scientific state on readback."""

import json

import pytest
import torch

from . import checkpoint_transition_v1 as migration
from .quote_repair_v1 import verify_seal
from .test_quote_handoff_v1 import checkpoint_fixture
from .quote_handoff_v1 import rebind_calibration
from .shards.contract import digest
from .wandb_identity_v1 import persist, resume_identity, signature


def fixture(tmp_path):
    """Include FP32 optimizer moments, BF16 parameters and byte RNG state."""
    saved, certificate, old, new = checkpoint_fixture()
    saved["model"]["bf16"] = torch.tensor([1.25, 2.5], dtype=torch.bfloat16)
    source = tmp_path / "checkpoint_epoch_022.pt"
    torch.save(saved, source)
    return source, certificate, old, new


def test_independent_copy_proves_state_bytes_and_retains_parent(tmp_path):
    source, certificate, old, new = fixture(tmp_path)
    before = migration.file_hash(source)
    proof = migration.prepare_copy(source, tmp_path / "prepared", certificate, old, new)
    verify_seal(proof, "hash")
    assert proof["state"] == "prepared-not-activated"
    assert proof["source_sha256"] == before == migration.file_hash(source)
    readback = torch.load(proof["target"], weights_only=False)
    parent = torch.load(source, weights_only=False)
    for key in migration.PRESERVED:
        assert migration.state_hash(readback[key]) == migration.state_hash(parent[key])
    assert migration.file_hash(proof["target"]) == proof["target_sha256"]
    assert readback["history"] == [] and parent["history"]
    with pytest.raises(FileExistsError):
        migration.prepare_copy(source, tmp_path / "prepared", certificate, old, new)


def test_readback_tensor_change_fails_before_publication(tmp_path, monkeypatch):
    source, certificate, old, new = fixture(tmp_path)
    original_load = torch.load

    def corrupted(path, **kwargs):
        result = original_load(path, **kwargs)
        if str(path).endswith(".partial"):
            result["optimizer"]["state"][0]["exp_avg"][0] += 1
        return result

    monkeypatch.setattr(torch, "load", corrupted)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="serialization changed"):
        migration.prepare_copy(source, output, certificate, old, new)
    assert not (output / "checkpoint_prepared.pt").exists()
    assert not (output / "checkpoint-preparation.json").exists()


def test_parent_replacement_fails_closed(tmp_path, monkeypatch):
    source, certificate, old, new = fixture(tmp_path)
    original_hash = migration.file_hash
    calls = 0

    def changing(path):
        nonlocal calls
        if path == source:
            calls += 1
            if calls == 2:
                return "f" * 64
        return original_hash(path)

    monkeypatch.setattr(migration, "file_hash", changing)
    with pytest.raises(ValueError, match="Source checkpoint changed"):
        migration.prepare_copy(source, tmp_path / "rejected", certificate, old, new)


def test_state_hash_binds_types_shape_dtype_and_sequence_order():
    first = migration.state_hash(dict(a=torch.tensor([1.0, 2.0]), b=[1, 2]))
    assert first == migration.state_hash(dict(b=[1, 2], a=torch.tensor([1.0, 2.0])))
    for different in (
        dict(a=torch.tensor([[1.0, 2.0]]), b=[1, 2]),
        dict(a=torch.tensor([1.0, 2.0], dtype=torch.float64), b=[1, 2]),
        dict(a=torch.tensor([1.0, 2.0]), b=[2, 1]),
        dict(a=torch.tensor([1.0, 2.0]), b=(1, 2)),
    ):
        assert first != migration.state_hash(different)
    with pytest.raises(ValueError, match="Unsupported checkpoint value"):
        migration.state_hash(object())


def reporting_fixture(tmp_path, reset_schedule=False):
    """Prepare a genuine copied checkpoint and a sealed target split/calibration."""
    source, certificate, old, _ = fixture(tmp_path)
    manifest = dict(
        dataset=str(tmp_path / "new-data"),
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
    split, units = tmp_path / "new-split.json", tmp_path / "new-calibration.json"
    split.write_text(json.dumps(manifest))
    units.write_text(json.dumps(calibration))
    original = dict(
        mode="train",
        model="large",
        learning_rate=0.001,
        dataset="old-data",
        split_manifest="old-split",
        calibration="old-units",
    )
    (tmp_path / "manifest.json").write_text(json.dumps(original))
    identity = dict(
        id="original",
        name="train-01",
        root=str(tmp_path),
        project="project",
        entity="team",
        signature=signature(original),
    )
    persist(tmp_path, identity)
    output = tmp_path / "prepared"
    proof = migration.prepare_copy(source, output, certificate, old, calibration,
                                   reset_schedule=reset_schedule)
    config = dict(
        original,
        resume=proof["target"],
        dataset=manifest["dataset"],
        split_manifest=str(split),
        calibration=str(units),
    )
    return output, config, identity


def test_authorized_scheduler_restart_preserves_weights_moments_rng_and_identity(tmp_path):
    from .campaign_v1 import EpochCosine
    from .schedule_transition_v1 import validate

    output, config, original = reporting_fixture(tmp_path, reset_schedule=True)
    proof = json.loads((output / "checkpoint-preparation.json").read_text())
    saved = torch.load(proof["target"], weights_only=False)
    parent = torch.load(proof["source"], weights_only=False)
    for key in migration.PRESERVED:
        if key != "scheduler":
            assert migration.state_hash(saved[key]) == migration.state_hash(parent[key])
    assert "scheduler" not in proof["preserved"]
    plan = validate(saved)
    assert plan["start_epoch"] == 22 and saved["contract"]["epochs"] == 62
    schedule = EpochCosine(1, 0, 1, 0)
    schedule.load_state_dict(saved["scheduler"])
    assert schedule.value() == pytest.approx(3e-4)
    schedule.epoch = 39
    assert schedule.value() == pytest.approx(5e-5)
    config.update(epochs=62, learning_rate=3e-4, min_learning_rate=5e-5, warmup_updates=0)
    (tmp_path / "metrics.jsonl").write_text(json.dumps(dict(step=10500)) + "\n")
    revised = migration.prepare_reporting_identity(output, config, "project")
    assert revised["id"] == original["id"] and revised["name"] == "train-01"
    assert revised["global_step_offset"] == 501
    _, resumed = migration.resume_inputs(config["resume"], output / "checkpoint-preparation.json", tmp_path.parent)
    assert resumed["epochs"] == 62
    saved["scheduler"]["updates"] += 1
    with pytest.raises(ValueError, match="position"):
        validate(saved)


def test_reset_bridge_refuses_uncertified_rate(tmp_path):
    output, config, _ = reporting_fixture(tmp_path, reset_schedule=True)
    config.update(epochs=62, learning_rate=0.002, min_learning_rate=5e-5, warmup_updates=0)
    with pytest.raises(ValueError, match="authorized scheduler"):
        migration.prepare_reporting_identity(output, config, "project")


def test_explicit_reporting_bridge_preserves_original_remote_id_and_lease_root(
    tmp_path,
):
    output, config, original = reporting_fixture(tmp_path)
    revised = migration.prepare_reporting_identity(output, config, "project")
    assert revised["id"] == original["id"] and revised["root"] == original["root"]
    assert (
        revised["name"] == "train-01" and revised["signature"] != original["signature"]
    )
    assert resume_identity(config, "project") == revised
    assert json.loads((tmp_path / "wandb-identity.json").read_text()) == original
    assert (output / "reporting-bridge.json").is_file()
    parent, resumed_config = migration.resume_inputs(
        config["resume"], output / "checkpoint-preparation.json", tmp_path.parent
    )
    assert parent == tmp_path and resumed_config["dataset"] == config["dataset"]
    with pytest.raises(ValueError, match="owned campaign"):
        migration.resume_inputs(
            config["resume"], output / "checkpoint-preparation.json", tmp_path
        )
    with pytest.raises(FileExistsError):
        migration.prepare_reporting_identity(output, config, "project")


def test_reporting_bridge_rejects_hyperparameter_or_loader_drift(tmp_path):
    output, config, _ = reporting_fixture(tmp_path)
    with pytest.raises(ValueError, match="Only certified"):
        migration.prepare_reporting_identity(
            output, dict(config, learning_rate=0.002), "project"
        )
    manifest_path = tmp_path / "new-split.json"
    changed = json.loads(manifest_path.read_text())
    changed["cutoff_us"] += 1
    manifest_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="actual loader split"):
        migration.prepare_reporting_identity(output, config, "project")
    assert not (output / "wandb-identity.json").exists()


def test_transition_refuses_a_still_live_parent_child(tmp_path):
    """A dead controller's orphan child must not compete with migrated training."""
    import psutil

    campaign = tmp_path / "campaign"
    parent = campaign / "train-05"
    parent.mkdir(parents=True)
    output, config, _ = reporting_fixture(parent)
    migration.prepare_reporting_identity(output, config, "project")
    process = psutil.Process()
    (campaign / "train-05-child.json").write_text(
        json.dumps(dict(pid=process.pid, created=process.create_time()))
    )
    with pytest.raises(RuntimeError, match="still alive"):
        migration.resume_inputs(
            config["resume"], output / "checkpoint-preparation.json", campaign
        )
