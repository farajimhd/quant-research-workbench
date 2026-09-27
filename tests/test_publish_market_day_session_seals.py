from __future__ import annotations

from datetime import date

import pytest

from scripts.clickhouse import publish_market_day_session_seals as publisher


def test_publisher_requires_explicit_confirmation_before_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(publisher, "publish_build_sessions",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(SystemExit) as exc:
        publisher.main(["--build-id", "a" * 64, "--apply"])
    assert exc.value.code == 2
    assert not calls


def test_publisher_dry_run_forwards_selected_date_and_never_applies(monkeypatch, capsys):
    observed = []

    def fake_publish(build_id, *, selected_days, apply):
        observed.append((build_id, selected_days, apply))
        return dict(sessions=1, committed_before=0, rows_unsealed_before=0,
                    absent_before=1, committed_now=0)

    monkeypatch.setattr(publisher, "publish_build_sessions", fake_publish)
    assert publisher.main(["--build-id", "a" * 64,
                           "--session", "2026-08-18"]) == 0
    assert observed == [("a" * 64, (date(2026, 8, 18),), False)]
    assert "planned: sessions=1" in capsys.readouterr().out
