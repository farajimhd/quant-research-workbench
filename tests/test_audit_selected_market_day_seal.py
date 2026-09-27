from __future__ import annotations

from datetime import date

from scripts.clickhouse import audit_selected_market_day_seal as command


def test_selected_day_audit_cli_reports_exact_scope(monkeypatch, capsys):
    calls = []

    def fake_audit(build_id, day, *, compare_global):
        calls.append((build_id, day, compare_global))
        return 4700, 14100, "f" * 64

    monkeypatch.setattr(command, "audit", fake_audit)
    assert command.main(["--build-id", "a" * 64,
                         "--session", "2026-08-18"]) == 0
    assert calls == [("a" * 64, date(2026, 8, 18), False)]
    output = capsys.readouterr().out
    assert "tickers=4700 product_units=14100" in output
    assert "writes=0" in output
    assert "global_parity=not_run" in output
