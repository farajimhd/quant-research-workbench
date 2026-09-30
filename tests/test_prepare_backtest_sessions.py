import json
from pathlib import Path

import pytest

from scripts.clickhouse import prepare_backtest_sessions as campaign


def row(tmp_path):
    build = "a" * 64
    (tmp_path / f"{build}.json").write_text("{}")
    return {"session_date": "2026-07-30", "build_id": build,
            "archive_directory": str(tmp_path)}


def test_plan_rejects_duplicate_sessions(tmp_path):
    unit = row(tmp_path)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps([unit, unit]))
    with pytest.raises(ValueError, match="unique and chronological"):
        campaign.read_plan(path, tmp_path)


def test_stages_never_rebuild_core_or_run_backtest(tmp_path):
    stages = dict(campaign.stages(row(tmp_path), 4, tmp_path))
    assert len(stages) == 9
    assert "--archive-directory" in stages["execution-prices"]
    assert "--apply" not in stages["app-preflight"]
    assert "--apply" not in stages["verify"]
    assert all(args[0] != "build_market_day.py" for args in stages.values())


def test_failure_stops_dependent_stages_but_records_other_sessions(tmp_path, monkeypatch):
    first = row(tmp_path)
    second = {**first, "session_date": "2026-07-31"}
    calls = []
    class Child:
        def __init__(self, command, **kwargs):
            calls.append(command)
            self.returncode = 1 if "2026-07-30" in command else 0
        def poll(self):
            return self.returncode
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    output = tmp_path / "output"
    assert campaign.run([first, second], output, tmp_path, 4) == 1
    state = json.loads((output / "status.json").read_text())
    assert state["completed"] == ["2026-07-31"]
    assert len(state["failed"]) == 1
    assert len(calls) == 10
    assert not (output / "controller.lock").exists()


def test_stop_prevents_new_producer(tmp_path, monkeypatch):
    unit = row(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "STOP").touch()
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda *_a, **_k: pytest.fail("writer admitted"))
    assert campaign.run([unit], output, tmp_path, 4) == 130
    assert json.loads((output / "status.json").read_text())["status"] == "stopped"


def test_existing_controller_lock_prevents_duplicate_campaign(tmp_path):
    unit = row(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "controller.lock").write_text("other-owner")
    with pytest.raises(FileExistsError):
        campaign.run([unit], output, tmp_path, 4)
    assert (output / "controller.lock").read_text() == "other-owner"
