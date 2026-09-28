"""Bounded V7 derivative command never writes during dry-run or off host."""
from __future__ import annotations

from scripts.clickhouse import publish_strategy_one_v7_intervals as command


def test_source_retry_uses_fresh_reader_and_never_retries_insert(monkeypatch):
    from http.client import IncompleteRead
    monkeypatch.setattr(command, "sleep", lambda _seconds: None)

    class Reader:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    first, second = Reader(), Reader()
    replacements = []
    def derive(reader):
        if reader is first:
            raise IncompleteRead(b"")
        return "derived"
    result, active = command._derive_with_source_retry(
        derive, first, lambda: second, replacements.append)
    assert result == "derived" and active is second
    assert first.closed and not second.closed and replacements == [second]


def test_dry_run_has_no_connection(capsys, monkeypatch):
    monkeypatch.setattr(command, "publish_session", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("dry-run connected")))
    assert command.main(["--session-date", "2026-08-18", "--ticker", "ABCD"]) == 0
    assert "no connection or write" in capsys.readouterr().out


def test_apply_requires_managed_host(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "OTHER-HOST")
    monkeypatch.setattr(command, "publish_session", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("off-host connected")))
    assert command.main(["--apply", "--confirm-v7-interval-publication"]) == 1
    assert "managed workstation" in capsys.readouterr().err


def test_apply_passes_bounded_scope(capsys, monkeypatch):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    called = []
    monkeypatch.setattr(command, "publish_session", lambda **kwargs: called.append(kwargs))
    assert command.main(["--session-date", "2026-08-18", "--ticker", "ABCD",
                         "--workers", "2", "--apply",
                         "--confirm-v7-interval-publication"]) == 0
    assert called == [{"session_date": "2026-08-18", "build_id": "",
                       "ticker": "ABCD", "max_tickers": 0, "workers": 2}]
    assert capsys.readouterr().err == ""
