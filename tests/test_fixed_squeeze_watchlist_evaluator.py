from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from src.backend.fixed_squeeze_watchlist_evaluator import (
    ScannerBoundaryRef, _matches, evaluate_attested_squeeze_watchlist,
)
from src.backend.historical_watchlist_plan import compile_historical_watchlist_plan
from src.backend.trading_configuration_service import _default_draft


AT = datetime(2026, 8, 18, 14, 0, 0, tzinfo=timezone.utc)
TICKERS = tuple(chr(65 + index) for index in range(11))


def plan():
    return compile_historical_watchlist_plan(
        _default_draft(), "squeeze-tradable-candidates",
        start=AT, end=AT + timedelta(minutes=1),
    )


def row(ticker, rank, *, score="60"):
    return {
        "ticker": ticker, "liquidity_rank": rank, "liquidity_score": score,
        "last_price": "10", "bid": "9.99", "ask": "10.01",
        "day_dollar_volume": "1000000", "day_volume": "100000",
        "trade_rate_10s": "1", "trade_rate_60s": "0.5",
    }


def fake_boundaries(monkeypatch, frames):
    def load(_client, _keeper, boundary_id, *, market_plan_token,
             source_revision_token, boundary_at):
        assert market_plan_token == "a" * 64
        assert source_revision_token == "source"
        index = int(boundary_id)
        assert boundary_at == AT + timedelta(seconds=index)
        rows = frames[index]
        return {"market_row_count": len(rows)}, tuple(rows)
    monkeypatch.setattr(
        "src.backend.fixed_squeeze_watchlist_evaluator.load_attested_scanner_boundary", load)


def refs(count):
    return tuple(ScannerBoundaryRef(str(i), "source", AT + timedelta(seconds=i))
                 for i in range(count))


def evaluate(p, references):
    return evaluate_attested_squeeze_watchlist(
        None, None, p, references, expected_tickers=TICKERS,
        market_plan_token="a" * 64, query_sha256="b" * 64,
    )


def test_top_ten_uses_plan_score_ticker_tie_not_global_qmd_rank(monkeypatch):
    # Global QMD rank uses additional tie-breaks; Watchlist does not.
    frames = [[row(ticker, 11 - i) for i, ticker in enumerate(TICKERS)]]
    fake_boundaries(monkeypatch, frames)
    snapshots, transitions = evaluate(plan(), refs(1))
    assert tuple(candidate.ticker for candidate in snapshots[0].candidates) == TICKERS[:10]
    assert [candidate.rank for candidate in snapshots[0].candidates] == list(range(1, 11))
    assert len(transitions) == 10
    assert all(event.event == "added" and event.watchlist_id == "squeeze-tradable-candidates"
               for event in transitions)


def test_1s_transition_rank_and_removal_without_score_only_event(monkeypatch):
    first = [row(ticker, i + 1, score=str(100 - i))
             for i, ticker in enumerate(TICKERS)]
    second = deepcopy(first)
    second[0]["liquidity_score"] = "98.5"  # A moves below B; K remains outside.
    second[10]["last_price"] = "1"  # Seven-filter rejection is not a partial scan.
    fake_boundaries(monkeypatch, [first, second])
    _, transitions = evaluate(plan(), refs(2))
    assert [(event.ticker, event.event, event.rank) for event in transitions[10:]] == [
        ("A", "rank_changed", 2), ("B", "rank_changed", 1),
    ]
    # A score-only change with no ordering change does not emit a QMD rank delta.
    second[0]["liquidity_score"] = "99.5"
    fake_boundaries(monkeypatch, [first, second])
    assert len(evaluate(plan(), refs(2))[1]) == 10


def test_filter_loss_emits_removal_and_next_full_scope_member(monkeypatch):
    first = [row(ticker, i + 1, score=str(100 - i))
             for i, ticker in enumerate(TICKERS)]
    second = deepcopy(first)
    second[0]["day_volume"] = "99999"
    fake_boundaries(monkeypatch, [first, second])
    _, transitions = evaluate(plan(), refs(2))
    assert ("A", "removed", None) in [
        (event.ticker, event.event, event.rank) for event in transitions[10:]]
    assert ("K", "added", 10) in [
        (event.ticker, event.event, event.rank) for event in transitions[10:]]


@pytest.mark.parametrize("change", [
    {"last_price": "1.99"}, {"last_price": "50.01"},
    {"day_dollar_volume": "999999.99"}, {"day_volume": "99999"},
    {"trade_rate_10s": "0.99"}, {"trade_rate_60s": "0.49"},
    {"ask": "10.061"},
])
def test_all_seven_filters_are_closed(change):
    candidate = row("A", 1)
    assert _matches(candidate)
    candidate.update(change)
    assert not _matches(candidate)


def test_partial_scope_and_missing_cadence_fail_closed(monkeypatch):
    complete = [row(ticker, i + 1) for i, ticker in enumerate(TICKERS)]
    fake_boundaries(monkeypatch, [complete[:-1]])
    with pytest.raises(ValueError, match="universe is incomplete"):
        evaluate(plan(), refs(1))
    fake_boundaries(monkeypatch, [complete])
    with pytest.raises(ValueError, match="contiguous completed 1s"):
        evaluate(plan(), (ScannerBoundaryRef("0", "source", AT),
                          ScannerBoundaryRef("2", "source", AT + timedelta(seconds=2))))


def test_rehashed_unsupported_rule_or_ttl_still_fails_closed():
    for mutation in ("ttl", "rule"):
        p = deepcopy(plan())
        if mutation == "ttl":
            p["membership_ttl_ms"] = 200000
        else:
            p["rule_sets"][0]["conditions"][0]["value"] = 1.0
        body = {key: value for key, value in p.items() if key != "plan_hash"}
        p["plan_hash"] = "sha256:" + sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()).hexdigest()
        with pytest.raises(ValueError, match="unsupported|seven-filter"):
            evaluate(p, refs(1))
