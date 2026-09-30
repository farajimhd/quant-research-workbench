"""V4 saved review exposes only cold-verified normalized terminal evidence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend import backtest_v4_saved_review as review
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.backend.typed_backtest_review_core import AuditedSessionCache
from src.backend.backtest_terminal_v2_fence import seal_v2_row


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
    monkeypatch.setattr(review, "load_committed_initial_cash",
                        lambda *_a, **_k: order.append("initial_cash") or 10000.0)
    monkeypatch.setattr(review, "load_verified_v4_prefix", prefix)
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot", snapshot)
    monkeypatch.setattr(review, "load_latest_backtest_cursor", cursor)
    monkeypatch.setattr(review, "load_typed_event_page", events)
    monkeypatch.setattr(review, "_terminal_financial_accounts", lambda *_a:
                        {"SIM-01-A": {"net_liquidation": 100000.0}})
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = _read(after_sequence=1)
    assert order == ["context", "prefix", "snapshot", "cursor", "initial_cash", "events"]
    assert page["run"]["initial_cash"] == 10000.0
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


def test_terminal_page_rejects_unknown_numbered_strategy(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a:
                        {**_context(), "strategy_revision": 3})
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a:
                        pytest.fail("Read unrelated run"))
    with pytest.raises(ValueError, match="only immutable Strategy 1"):
        _read()


def test_archived_v4_without_cursor_discloses_missing_clock(monkeypatch):
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a: _context())
    monkeypatch.setattr(review, "load_committed_initial_cash",
                        lambda *_a, **_k: 10000.0)
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_a:
                        V4CommittedPrefix(RUN, 2, BATCH, "start", "completed", (BATCH,)))
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot", lambda *_a, **_k:
                        {"state_hash": "a" * 64, "state_revision": 1,
                         "snapshot_at": "2026-08-18T13:30:00+00:00"})
    monkeypatch.setattr(review, "load_latest_backtest_cursor", lambda *_a: None)
    monkeypatch.setattr(review, "load_typed_event_page", lambda *_a, **_k: ())
    monkeypatch.setattr(review, "_terminal_financial_accounts", lambda *_a:
                        {"SIM-01-A": {"net_liquidation": 100000.0}})
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = _read(after_sequence=2)
    assert page["market_cursor"] is None
    assert page["market_cursor_verified"] is False
    assert "exact processed-through clock is unavailable" in page["limitations"][0]


def test_subsequent_page_reuses_audited_prefix_but_rechecks_head(monkeypatch):
    calls = {"audit": 0, "snapshot": 0, "head": 0, "events": 0}
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_a: _context())
    monkeypatch.setattr(review, "load_committed_initial_cash",
                        lambda *_a, **_k: 10000.0)

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
    monkeypatch.setattr(review, "_terminal_financial_accounts", lambda *_a:
                        {"SIM-01-A": {"net_liquidation": 100000.0}})
    monkeypatch.setattr(review, "_head_matches", head)
    monkeypatch.setattr(review, "load_typed_event_page", events)
    cache = AuditedSessionCache()
    client = Client()
    review.load_v4_terminal_review_page(client, RUN, limit=1, cache=cache)
    review.load_v4_terminal_review_page(client, RUN, after_sequence=1,
                                        limit=1, cache=cache)
    assert calls == {"audit": 1, "snapshot": 1, "head": 4, "events": 2}


def test_financial_account_verifies_integral_float64_wire_values(monkeypatch):
    sealed = seal_v2_row("trading_backtest_account_snapshot_v2", {
        "record_id": RUN, "run_id": RUN, "event_month": "2026-08-01",
        "batch_id": BATCH, "snapshot_id": BATCH,
        "account_id": "SIM-01-A", "currency": "USD",
        "source_timestamp_ms": 1787069400000,
        "net_liquidation": 100000.0, "total_cash_value": 100000.0,
        "buying_power": 100000.0, "gross_position_value": 0.0,
        "available_funds": 100000.0, "excess_liquidity": 100000.0,
        "expected_position_count": 0, "position_set_sha256": "a" * 64,
    })
    monkeypatch.setattr(review, "_rows", lambda _client, _sql:
                        [{**sealed, "gross_position_value": 0}])
    result = review._terminal_financial_accounts(Client(), _prefix(),
                                                 ("SIM-01-A",))
    assert result["SIM-01-A"]["gross_position_value"] == 0.0


def test_trade_history_uses_verified_prefix_and_independent_cursors(monkeypatch):
    observed = []
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    monkeypatch.setattr(review, "load_committed_execution_page",
                        lambda _client, prefix, **kw: (
                            observed.append(("fills", prefix, kw)) or
                            ({"sequence": 1, "execution_id": "fill-1"},)))
    monkeypatch.setattr(review, "load_committed_commission_page",
                        lambda _client, prefix, **kw: (
                            observed.append(("fees", prefix, kw)) or
                            ({"sequence": 2, "execution_id": "fill-1"},)))
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = review.load_v4_trade_history_page(Client(), RUN, limit=1)
    assert [(kind, params) for kind, _, params in observed] == [
        ("fills", {"after_sequence": 0, "limit": 1}),
        ("fees", {"after_sequence": 0, "limit": 1}),
    ]
    assert all(prefix is observed[0][1] for _, prefix, _ in observed)
    assert page["next_fill_sequence"] == 1
    assert page["next_commission_sequence"] == 2
    assert page["complete"] is False


def test_trade_history_rejects_out_of_prefix_cursor_before_rows(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    monkeypatch.setattr(review, "load_committed_execution_page",
                        lambda *_a, **_k: pytest.fail("Read after invalid cursor"))
    with pytest.raises(ValueError, match="exceeds the verified journal"):
        review.load_v4_trade_history_page(
            Client(), RUN, after_fill_sequence=3, cache=AuditedSessionCache())


def test_trade_history_rejects_head_change_after_rows(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    monkeypatch.setattr(review, "load_committed_execution_page",
                        lambda *_a, **_k: ())
    monkeypatch.setattr(review, "load_committed_commission_page",
                        lambda *_a, **_k: ())
    monkeypatch.setattr(review, "_head_matches", lambda *_a: False)
    with pytest.raises(RuntimeError, match="head changed"):
        review.load_v4_trade_history_page(Client(), RUN)


def test_saved_chart_keeps_verified_open_position_quantity(monkeypatch):
    monkeypatch.setattr(review, "load_cached_v4_performance_report", lambda *_a: {
        "run_id": RUN, "verified_sequence": 2,
        "position_lifecycles": [{
            "episode_id": "episode-1", "instrument": {"symbol": "BBNX"},
            "account_id": "SIM-01-A", "opened_at": "2026-08-18T12:00:00+00:00",
            "entry_price": 3.0, "side": "LONG", "quantity": 100,
            "current_quantity": 75, "status": "open", "protection_timeline": [],
        }],
    })
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a: {"prefix": _prefix()})
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    monkeypatch.setattr(
        "src.trading_runtime.arte_intent_projection.load_committed_strategy_intent_page",
        lambda *_a, **_k: (),
    )
    result = review.load_v4_chart_trades(Client(), RUN, "BBNX")
    assert result["position_lifecycles"][0]["current_quantity"] == 75
    assert result["position_lifecycles"][0]["status"] == "open"


def test_order_history_reads_independent_verified_pages(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    monkeypatch.setattr(review, "load_committed_order_command_page", lambda *_a, **_k:
                        ({"sequence": 1, "command_id": "command-1"},))
    monkeypatch.setattr(review, "load_committed_order_transition_page", lambda *_a, **_k:
                        ({"sequence": 2, "command_id": "command-1"},))
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    page = review.load_v4_order_history_page(Client(), RUN, limit=1)
    assert page["next_command_sequence"] == 1
    assert page["next_transition_sequence"] == 2
    assert page["complete"] is False


def test_v4_performance_derives_net_trade_from_typed_fills_and_final_fees(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    base = {"account_id": "SIM-01-A", "conid": 10, "ticker": "ABC",
            "currency": "USD", "exchange": "SIM", "broker_order_id": "",
            "client_order_id": "", "strategy_id": "early-squeeze-strategy",
            "strategy_revision": 1, "setup": "", "exit_reason": "",
            "signal_price": None, "arrival_midpoint": None,
            "planned_risk": None}
    fills = (
        {**base, "sequence": 1, "execution_id": "buy", "side": "B", "broker_order_id": "entry-1",
         "quantity": "10", "price": "2", "source_event_time": "2026-08-18 08:00:00.000000000"},
        {**base, "sequence": 3, "execution_id": "sell", "side": "S", "broker_order_id": "exit-1",
         "quantity": "10", "price": "3", "source_event_time": "2026-08-18 08:01:00.000000000"},
    )
    fees = (
        {"sequence": 2, "execution_id": "buy", "account_id": "SIM-01-A",
         "commission": "1", "currency": "USD", "status": "final"},
        {"sequence": 4, "execution_id": "sell", "account_id": "SIM-01-A",
         "commission": "1", "currency": "USD", "status": "final"},
    )
    monkeypatch.setattr(review, "load_committed_execution_page", lambda *_a, **_k: fills)
    monkeypatch.setattr(review, "load_committed_commission_page", lambda *_a, **_k: fees)
    monkeypatch.setattr(review, "_saved_protection_events", lambda *_a: [{
        "sequence": 2, "event_time": "2026-08-18T08:00:30+00:00",
        "account_id": "SIM-01-A", "entry_order_ids": ["entry-1"],
        "phase": "effective", "kind": "stop", "order_id": "exit-1",
        "price": 1.9, "active": True,
    }])
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    result = review.load_v4_performance_report(Client(), RUN)
    assert result["fill_count"] == result["fee_count"] == 2
    assert float(result["report"]["summary"]["net_pnl"]) == 8.0
    assert result["report"]["summary"]["episode_count"] == 1
    assert result["report"]["execution"]["order_count"] is None
    assert result["position_lifecycles"][0]["presentation_exit_reason"] == "stop_hit"
    assert len(result["position_lifecycles"][0]["protection_timeline"]) == 1


def test_v4_performance_orders_opposing_same_timestamp_fills_by_journal_sequence(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    base = {"account_id": "SIM-01-A", "conid": 10, "ticker": "ABC",
            "currency": "USD", "exchange": "SIM", "broker_order_id": "",
            "client_order_id": "", "strategy_id": "early-squeeze-strategy",
            "strategy_revision": 1, "setup": "", "exit_reason": "",
            "signal_price": None, "arrival_midpoint": None,
            "planned_risk": None,
            "source_event_time": "2026-08-18 08:00:00.000000000"}
    # Lexical execution-ID order is deliberately opposite to the committed
    # fill order; source timestamps alone cannot resolve this bucket.
    fills = (
        {**base, "sequence": 1, "execution_id": "z-buy", "side": "B",
         "quantity": "10", "price": "2"},
        {**base, "sequence": 3, "execution_id": "a-sell", "side": "S",
         "quantity": "10", "price": "3"},
    )
    fees = (
        {"sequence": 2, "execution_id": "z-buy", "account_id": "SIM-01-A",
         "commission": "1", "currency": "USD", "status": "final"},
        {"sequence": 4, "execution_id": "a-sell", "account_id": "SIM-01-A",
         "commission": "1", "currency": "USD", "status": "final"},
    )
    monkeypatch.setattr(review, "load_committed_execution_page", lambda *_a, **_k: fills)
    monkeypatch.setattr(review, "load_committed_commission_page", lambda *_a, **_k: fees)
    monkeypatch.setattr(review, "_saved_protection_events", lambda *_a: [])
    monkeypatch.setattr(review, "_head_matches", lambda *_a: True)
    result = review.load_v4_performance_report(Client(), RUN)
    assert result["fill_count"] == 2
    assert result["report"]["summary"]["episode_count"] == 1
    assert float(result["report"]["summary"]["net_pnl"]) == 8.0


def test_v4_performance_rejects_unfinalized_fee(monkeypatch):
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_a:
                        {"prefix": _prefix()})
    monkeypatch.setattr(review, "load_committed_execution_page", lambda *_a, **_k:
                        ({"sequence": 1, "execution_id": "buy"},))
    monkeypatch.setattr(review, "load_committed_commission_page", lambda *_a, **_k: ())
    with pytest.raises(RuntimeError, match="final fees"):
        review.load_v4_performance_report(Client(), RUN)
