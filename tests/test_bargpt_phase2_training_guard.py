"""Admission follows the current trainer and never writes its state."""

import json
import time

from research.bar_gpt.v5.phase2_shards.training_guard import TrainingGuard


def test_training_validation_and_stale_status_caps(tmp_path):
    attempt = tmp_path / "train-03"
    attempt.mkdir()
    controller = tmp_path / "progress.json"
    controller.write_text(json.dumps(dict(child=dict(output=str(attempt)))))
    guard = TrainingGuard(tmp_path, 64, 4)
    assert guard.limit == 4  # Startup cannot admit full work before reading evidence.
    for stage, age, expected in (
        ("training", 0, 64),
        ("validation", 0, 4),
        ("learning probe", 0, 4),
        ("training", 60, 4),
    ):
        status = attempt / "progress.json"
        status.write_text(
            json.dumps(dict(stage=stage, state="running", updated=time.time() - age))
        )
        before = status.read_bytes(), controller.read_bytes()
        guard.checked = float("-inf")
        assert guard.refresh() == expected
        assert before == (status.read_bytes(), controller.read_bytes())
    controller.write_text(json.dumps(dict(state="complete")))
    guard.checked = float("-inf")
    assert guard.refresh() == 64
