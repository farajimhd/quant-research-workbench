"""Verify automatic handoff arguments without starting processes or long training."""

from types import SimpleNamespace
import pytest

from research.bar_gpt.v5.overnight_v1 import Controller
from research.bar_gpt.v5.shards.phase1_contract import write_json


def test_metadata_sharing_race_retries_without_hiding_persistent_failures(monkeypatch):
    from research.bar_gpt.v5 import overnight_v1 as module

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    calls = []

    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("atomic replacement sharing conflict")
        return {"state": "running"}

    assert module.metadata_io(operation) == {"state": "running"}
    assert len(calls) == 3
    calls.clear()

    def denied():
        calls.append(1)
        raise PermissionError("persistent denial")

    with pytest.raises(PermissionError, match="persistent denial"):
        module.metadata_io(denied)
    assert len(calls) == 8

    def corrupt():
        raise ValueError("corrupt evidence")

    with pytest.raises(ValueError, match="corrupt evidence"):
        module.metadata_io(corrupt)


def test_successful_handoff_runs_only_short_checks_before_full_guarded_training(
    tmp_path, monkeypatch
):
    from research.bar_gpt.v5 import cohort_audit_v1, data_v1

    args = SimpleNamespace(
        output=tmp_path, dataset=tmp_path / "dataset", source_revision="fixture"
    )
    controller = Controller(args)
    monkeypatch.setattr(controller, "wait_for_shards", lambda: None)
    monkeypatch.setattr(
        cohort_audit_v1,
        "audit",
        lambda dataset, output, progress: write_json(output, dict(state="passed")),
    )
    manifest = dict(manifest_hash="fixture", counts=dict(train=80, validation=20))
    monkeypatch.setattr(data_v1, "build_split", lambda *a: manifest)
    monkeypatch.setattr(
        data_v1, "calibrate", lambda *a: (None, dict(split_hash="fixture"))
    )
    calls = []

    def child(label, arguments):
        calls.append((label, arguments))
        output = tmp_path / label
        output.mkdir()
        write_json(output / "progress.json", dict(state="complete"))
        if label == "profile":
            write_json(
                output / "results.json",
                dict(
                    results=[
                        dict(
                            status="complete",
                            loss=0.5,
                            excluded_no_price=0,
                            target_counts=[1] * 18,
                            batch_size=4096,
                            samples_per_second=100,
                        )
                    ]
                ),
            )
        elif label == "smoke":
            metrics = dict(loss=0.5, samples=10, target_counts=[1] * 18)
            write_json(
                output / "results.json",
                dict(history=[dict(training=metrics, validation=metrics)]),
            )
            (output / "checkpoint_last.pt").touch()
        return output

    monkeypatch.setattr(controller, "child", child)
    controller.run()
    assert [name for name, _ in calls] == ["profile", "smoke", "train-01"]
    final = calls[-1][1]
    assert "--learning-guard" in final
    assert final[final.index("--train-steps") + 1] == "0"
    assert final[final.index("--epochs") + 1] == "50"
    assert controller.state["state"] == "complete"
