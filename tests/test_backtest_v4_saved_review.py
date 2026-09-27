"""V4 saved review exposes only cold-verified normalized terminal evidence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend import backtest_v4_saved_review as review
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


RUN = "00000000-0000-0000-0000-000000000001"
BATCH = "00000000-0000-0000-0000-000000000002"


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
    page = review.load_v4_terminal_review_page(object(), RUN, after_sequence=1)
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
        review.load_v4_terminal_review_page(object(), RUN)


def test_terminal_page_rejects_non_strategy_one(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a:
                        {**_context(), "strategy_revision": 2})
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a:
                        pytest.fail("Read unrelated run"))
    with pytest.raises(ValueError, match="only immutable Strategy 1"):
        review.load_v4_terminal_review_page(object(), RUN)
