"""HOD campaign CLI is dry-run-first and protects workstation authority."""

import pytest

from scripts.clickhouse import publish_strategy_one_hod as command


def test_dry_run_does_not_connect_or_write(capsys, monkeypatch):
    monkeypatch.setattr(command, "_certified_plan", lambda **_kwargs:
                        pytest.fail("dry run connected"))
    assert command.main([]) == 0
    output = capsys.readouterr()
    assert "DRY RUN" in output.out
    assert "no connection or write" in output.out
    assert output.err == ""


def test_apply_requires_explicit_confirmation():
    with pytest.raises(SystemExit) as exc:
        command.main(["--apply"])
    assert exc.value.code == 2


def test_failure_shows_stage_without_leaking_driver_message(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command, "verify_session", lambda **_kwargs:
                        (_ for _ in ()).throw(ValueError("private SQL detail")))
    assert command.main(["--verify-only"]) == 1
    error = capsys.readouterr().err
    assert "ValueError at" in error
    assert "private SQL detail" not in error


def test_known_seed_coverage_gap_reports_bounded_count(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command, "verify_session", lambda **_kwargs:
                        (_ for _ in ()).throw(ValueError(
                            "V7 prior coverage is missing or duplicated: "
                            "missing=1 ['2026-08-18:TEST'], duplicates=0, unexpected=[]")))
    assert command.main(["--verify-only"]) == 1
    assert "missing=1" in capsys.readouterr().err
