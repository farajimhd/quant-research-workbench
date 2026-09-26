"""Pivot campaign is producer-only and dry-run safe by default."""
import pytest

from scripts.clickhouse import publish_strategy_one_pivots as command


def test_dry_run_never_connects_or_writes(monkeypatch, capsys):
    monkeypatch.setattr(command, "_certified_plan", lambda **_kwargs:
                        pytest.fail("dry run accessed market authority"))
    assert command.main([]) == 0
    output = capsys.readouterr()
    assert "DRY RUN" in output.out
    assert "no connection or write" in output.out
    assert output.err == ""


def test_writer_does_not_reuse_idle_socket_across_detector_work(monkeypatch):
    captured = []
    monkeypatch.setattr(command, "ClickHouseHttpClient", lambda *args, **kwargs:
                        captured.append((args, kwargs)) or object())
    command._writer("private")
    assert captured[0][1]["persistent"] is False


def test_apply_requires_workstation_and_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(command.platform, "node", lambda: "LAPTOP")
    with pytest.raises(SystemExit):
        command.main(["--apply"])
    assert command.main(["--apply", "--confirm-pivot-publication"]) == 1
    assert "managed workstation" in capsys.readouterr().err


def test_apply_reports_verified_campaign_result(monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    calls = []
    monkeypatch.setattr(command, "publish_session", lambda **kwargs:
                        calls.append(kwargs) or {"published": 1})
    assert command.main(["--apply", "--confirm-pivot-publication",
                         "--session-date", "2026-08-18", "--workers", "4"]) == 0
    assert calls == [{"session_date": "2026-08-18",
                      "build_id": "", "workers": 4}]


def test_failure_output_exposes_only_owned_stage_or_exception_type(monkeypatch, capsys):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    def failed(**_kwargs):
        raise RuntimeError("private SQL with credential")
    monkeypatch.setattr(command, "publish_session", failed)
    assert command.main(["--apply", "--confirm-pivot-publication"]) == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "private SQL" not in output.err
