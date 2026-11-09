"""Removing phase two must retain publication of the phase-one handoff."""

import json
import sys

import pytest

from . import quote_repair_campaign_v1 as campaign


@pytest.mark.parametrize("phase_one_only", [False, True])
def test_completed_phase_one_still_requests_handoff(
    tmp_path, monkeypatch, phase_one_only
):
    setup = tmp_path / "quote-source-first-setup"
    names = campaign.DATASETS[:2] if phase_one_only else campaign.DATASETS
    for _, name in names:
        target = tmp_path / (name + "-quote-v4")
        target.mkdir()
        (target / "repair-result.json").write_text("{}")
        (target / "repair-progress.json").write_text(
            json.dumps(dict(state="complete", completed=1, total=1))
        )
    checked = []
    monkeypatch.setattr(
        campaign, "require_complete", lambda path: checked.append(path.name)
    )
    cohorts = []
    monkeypatch.setattr(
        campaign,
        "cohort",
        lambda root, suffix: cohorts.append(suffix) or root / "cohort.json",
    )

    class Telemetry:
        url = "disabled"

        def __init__(self, *args, **kwargs):
            pass

        def update(self, value):
            pass

        def finish(self):
            pass

    monkeypatch.setattr(campaign, "RepairTelemetry", Telemetry)
    monkeypatch.setattr(
        campaign.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Completed datasets must not launch a child"),
    )
    argv = [
        "repair",
        "--root",
        str(tmp_path),
        "--source-revision",
        "test",
        "--source-first",
    ]
    if phase_one_only:
        argv.append("--phase-one-only")
    monkeypatch.setattr(sys, "argv", argv)
    assert campaign.main() == 0
    state = json.loads((setup / "campaign.json").read_text())
    assert [item["name"] for item in state["datasets"]] == [label for label, _ in names]
    assert state["state"] == "complete"
    assert len(cohorts) == 1
    handoff = json.loads((setup / "training-handoff-needed.json").read_text())
    assert handoff["checkpoint_boundary_only"] and handoff["wandb_id"] == "1rpdtas2"
    if phase_one_only:
        assert not any("phase2" in name for name in checked)
