"""V4 saved review exposes only cold-verified normalized terminal evidence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend import backtest_v4_saved_review as review
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.backend.typed_backtest_review_core import AuditedSessionCache


RUN = "00000000-0000-0000-0000-000000000001"
BATCH = "00000000-0000-0000-0000-000000000002"


class Client:
    base_url = "http://example.test"
    user = "review"
    password = "test-only"


def _read(*, after_sequence=0):
    return review.load_v4_terminal_review_page(
        Client(), RUN, after_sequence=after_sequence,
        cache=AuditedSessionCache())


def _context():
    return {
        "run_id": RUN, "mode": "backtest",
        "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
        "evaluation_interval_ms": 100, "session_date": "2026-08-18",
        "account_ids": ("SIM-01-A",),
    }


def _prefix():
    return V4CommittedPrefix(RUN, 2, BATCH, "2026-08-18:34200000",
                             "completed", (BATCH,))


def test_terminal_page_requires_verified_context_prefix_and_snapshot(monkeypatch):
    order = []

    def context(*_a):
        order.append("context")
        return _context()

    def prefix(*_a):
        order.append("prefix")
        return _prefix()

    def snapshot(*_a, **_k):
        order.append("snapshot")
        return {"state_hash": "a" * 64, "state_revision": 1,
                "snapshot_at": "2026-08-18T13:30:00+00:00"}

    def cursor(*_a):
        order.append("cursor")
        return {"session_date": "2026-08-18", "boundary_ms": 34200000}

    def events(*_a, **_k):
        order.append("events")
        return (SimpleNamespace(event={"sequence": 2}, detail_family=None,
                                detail=None),)

    monkeypatch.setattr(review, "load_typed_run_context", context)
    monkeypatch.setattr(review, "load_verified_v4_prefix", prefix)
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot", snapshot)
    monkeypatch.setattr(review, "load_latest_backtest_cursor", cursor)
    monkeypatch.setattr(review, "load_typed_event_page", events)
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = _read(after_sequence=1)
    assert order == ["context", "prefix", "snapshot", "cursor", "events"]
    assert page["verified_sequence"] == 2
    assert page["complete"] is True
    assert page["resume_supported"] is False
    assert page["accounts"]["SIM-01-A"] == {
        "state_hash": "a" * 64, "state_revision": 1,
        "snapshot_at": "2026-08-18T13:30:00+00:00"}


def test_terminal_page_fails_closed_before_event_exposure(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a: _context())
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a: None)
    monkeypatch.setattr(review, "load_typed_event_page", lambda *_a, **_k:
                        pytest.fail("Unverified events were exposed"))
    with pytest.raises(ValueError, match="cold-verified terminal"):
        _read()


def test_terminal_page_rejects_non_strategy_one(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a:
                        {**_context(), "strategy_revision": 2})
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a:
                        pytest.fail("Read unrelated run"))
    with pytest.raises(ValueError, match="only immutable Strategy 1"):
        _read()


def test_archived_v4_without_cursor_discloses_missing_clock(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a: _context())
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a:
                        V4CommittedPrefix(RUN, 2, BATCH, "start", "completed", (BATCH,)))
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot", lambda *_a, **_k:
                        {"state_hash": "a" * 64, "state_revision": 1,
                         "snapshot_at": "2026-08-18T13:30:00+00:00"})
    monkeypatch.setattr(review, "load_latest_backtest_cursor", lambda *_a: None)
    monkeypatch.setattr(review, "load_typed_event_page", lambda *_a, **_k: ())
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = _read(after_sequence=2)
    assert page["market_cursor"] is None
    assert page["market_cursor_verified"] is False
    assert "exact processed-through clock is unavailable" in page["limitations"][0]


def test_subsequent_page_reuses_audited_prefix_but_rechecks_head(monkeypatch):
    calls = {"audit": 0, "snapshot": 0, "head": 0, "events": 0}
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a: _context())

    def audited(*_a):
        calls["audit"] += 1
        return _prefix()

    def snapshot(*_a, **_k):
        calls["snapshot"] += 1
        return {"state_hash": "a" * 64, "state_revision": 1,
                "snapshot_at": "2026-08-18T13:30:00+00:00"}

    def head(*_a):
        calls["head"] += 1
        return True

    def events(*_a, **_k):
        calls["events"] += 1
        return (SimpleNamespace(event={"sequence": calls["events"]},
                                detail_family=None, detail=None),)

    monkeypatch.setattr(review, "load_verified_v4_prefix", audited)
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot", snapshot)
    monkeypatch.setattr(review, "load_latest_backtest_cursor", lambda *_a:
                        {"session_date": "2026-08-18", "boundary_ms": 34200000})
    monkeypatch.setattr(review, "_head_matches", head)
    monkeypatch.setattr(review, "load_typed_event_page", events)
    cache = AuditedSessionCache()
    client = Client()
    review.load_v4_terminal_review_page(client, RUN, limit=1, cache=cache)
    review.load_v4_terminal_review_page(client, RUN, after_sequence=1,
                                        limit=1, cache=cache)
    assert calls == {"audit": 1, "snapshot": 1, "head": 4, "events": 2}
