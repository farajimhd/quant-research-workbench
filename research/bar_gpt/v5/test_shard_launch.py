"""Regression checks for the real v6 migration-to-generation launch failure."""

import json

import pytest

from research.bar_gpt.v5 import run_balanced_shards as launcher
from research.bar_gpt.v5.shards.contract import digest, write_json


@pytest.mark.parametrize("option", [["--query-memory-gib", "1"], ["--query-memory-gib=1.0"]])
def test_explicit_default_reuses_the_migration_contract(option):
    """Both CLI forms must produce the exact scalar encoding used by prepare-only."""
    prepared = launcher.arguments([])
    resumed = launcher.arguments(launcher.canonical_arguments(option))
    assert digest(dict(memory=prepared.query_memory_gib)) == digest(dict(memory=resumed.query_memory_gib))
    assert int(resumed.query_memory_gib * 1024**3) == 1024**3


def test_nondefault_memory_limit_is_preserved():
    """Normalization cannot bypass a real campaign configuration change."""
    option = ["--query-memory-gib", "2"]
    assert launcher.canonical_arguments(option) == option
    assert launcher.arguments(option).query_memory_gib == 2


def test_startup_failure_publishes_terminal_status_and_retains_counts(tmp_path, monkeypatch):
    """A source/contract failure must be visible while immutable sample counts survive."""
    write_json(tmp_path / "progress.json", dict(state="planning", samples=123, bytes=456))

    def fail(argv):
        raise ValueError("Campaign contract changed")

    monkeypatch.setattr(launcher, "build_main", fail)
    with pytest.raises(ValueError, match="contract changed"):
        launcher.main(["--output", str(tmp_path)])
    snapshot = json.loads((tmp_path / "progress.json").read_text())
    assert snapshot["state"] == "failed" and snapshot["samples"] == 123 and snapshot["bytes"] == 456
    assert "contract changed" in snapshot["error"] and snapshot["updated"] > 0


def test_duplicate_controller_cannot_overwrite_owner_status(tmp_path, monkeypatch):
    """OS lock rejection is not a failure of the controller already writing shards."""
    original = dict(state="running", samples=123)
    write_json(tmp_path / "progress.json", original)

    def locked(argv):
        raise PermissionError("controller lock is held")

    monkeypatch.setattr(launcher, "build_main", locked)
    with pytest.raises(PermissionError):
        launcher.main(["--output", str(tmp_path)])
    assert json.loads((tmp_path / "progress.json").read_text()) == original
