"""Verify automatic handoff arguments without starting processes or long training."""

from types import SimpleNamespace

from research.bar_gpt.v5.overnight_v1 import Controller
from research.bar_gpt.v5.shards.phase1_contract import write_json


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
