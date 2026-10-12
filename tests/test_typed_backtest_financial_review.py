from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend import typed_backtest_financial_review as review
from src.trading_runtime.arte_journal_writer import (
    publish_typed_batch, publish_typed_run, publish_typed_run_context,
)
from tests.test_arte_journal_writer import MemoryClient, RUN, batch, run_context, run_row


def _stub(monkeypatch, *, mode="backtest", status="completed"):
    monkeypatch.setattr(review, "load_typed_run_context", lambda _client, _run: {
        "mode": mode, "session_date": "2026-08-18", "account_ids": ("DU1",),
    })
    monkeypatch.setattr(review, "load_committed_prefix", lambda _client, _run:
                        SimpleNamespace(status=status, last_sequence=120))
    monkeypatch.setattr(review, "load_latest_backtest_cursor", lambda _client, _prefix: {
        "session_date": "2026-08-18", "boundary_ms": 100,
    })
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot",
                        lambda _client, _prefix, *, account_id:
                        {"state_hash": "a" * 64, "account_id": account_id})


def test_typed_financial_review_uses_independent_fill_and_fee_cursors(monkeypatch) -> None:
    _stub(monkeypatch)
    requests = []

    def fills(_client, _prefix, *, after_sequence, limit):
        requests.append(("fill", after_sequence, limit))
        return ({"sequence": 10}, {"sequence": 11})

    def fees(_client, _prefix, *, after_sequence, limit):
        requests.append(("fee", after_sequence, limit))
        return ({"sequence": 100}, {"sequence": 101})

    monkeypatch.setattr(review, "load_committed_execution_page", fills)
    monkeypatch.setattr(review, "load_committed_commission_page", fees)
    page = review.load_typed_backtest_financial_page(
        object(), "run-1", after_fill_sequence=9,
        after_commission_sequence=99, limit=2)
    assert requests == [("fill", 9, 2), ("fee", 99, 2)]
    assert page["next_fill_sequence"] == 11
    assert page["next_commission_sequence"] == 101
    assert page["complete"] is False
    assert page["run"]["account_ids"] == ("DU1",)
    assert page["accounts"]["DU1"]["state_hash"] == "a" * 64


def test_typed_financial_review_rejects_wrong_mode(monkeypatch) -> None:
    _stub(monkeypatch, mode="live")
    with pytest.raises(ValueError, match="Backtest runs only"):
        review.load_typed_backtest_financial_page(object(), "run-1")


def test_comparison_collects_exact_native_pages_without_skipping_fees(monkeypatch):
    _stub(monkeypatch)
    prefix = SimpleNamespace(status="completed", last_sequence=120)
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_: prefix)
    monkeypatch.setattr(review, "load_committed_execution_page", lambda _client, _prefix,
        *, after_sequence, limit, exact_prices:
        tuple({"sequence": n} for n in (10, 11) if n > after_sequence)[:limit])
    monkeypatch.setattr(review, "load_committed_commission_page", lambda _client, _prefix,
        *, after_sequence, limit:
        tuple({"sequence": n} for n in (100, 101, 102) if n > after_sequence)[:limit])
    evidence = review.load_typed_backtest_financial_evidence(object(), "run-1", page_size=2, maximum_rows=5)
    assert [row["sequence"] for row in evidence["fills"]] == [10, 11]
    assert [row["sequence"] for row in evidence["commissions"]] == [100, 101, 102]
    assert evidence["complete"] is True
    with pytest.raises(ValueError, match="row bound"):
        review.load_typed_backtest_financial_evidence(object(), "run-1", page_size=2, maximum_rows=4)
    prefixes = iter((prefix, SimpleNamespace(status="completed", last_sequence=121)))
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_: next(prefixes))
    with pytest.raises(RuntimeError, match="changed its committed"):
        review.load_typed_backtest_financial_evidence(object(), "run-1", page_size=2, maximum_rows=5)
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_:
        SimpleNamespace(status="stopped", last_sequence=120))
    with pytest.raises(ValueError, match="completed market-cursor"):
        review.load_typed_backtest_financial_evidence(object(), "run-1", page_size=2, maximum_rows=5)


def test_exact_financial_review_requires_verified_v4_and_exact_fills(monkeypatch):
    _stub(monkeypatch)
    prefix = SimpleNamespace(status="completed", last_sequence=120)
    monkeypatch.setattr(review, "load_committed_prefix", lambda *_: pytest.fail("legacy prefix"))
    monkeypatch.setattr(review, "load_verified_v4_prefix", lambda *_: prefix)
    price = 10.250000000000002

    def fills(_client, received_prefix, *, after_sequence, limit, exact_prices):
        assert received_prefix is prefix
        assert exact_prices is True
        return ({"sequence": 10, "price": price},)

    monkeypatch.setattr(review, "load_committed_execution_page", fills)
    monkeypatch.setattr(review, "load_committed_commission_page", lambda *_args, **_kwargs: ())
    page = review.load_typed_backtest_financial_page(object(), "run-1", exact_prices=True)
    assert page["fills"][0]["price"].hex() == price.hex()


@pytest.mark.parametrize("flag", [1, None, "true"])
def test_financial_review_rejects_nonboolean_price_mode(flag):
    with pytest.raises(ValueError, match="invalid bounds"):
        review.load_typed_backtest_financial_page(object(), "run-1", exact_prices=flag)


def test_typed_financial_review_rejects_nonterminal_or_out_of_range(monkeypatch) -> None:
    _stub(monkeypatch, status="running")
    with pytest.raises(ValueError, match="terminal committed"):
        review.load_typed_backtest_financial_page(object(), "run-1")
    _stub(monkeypatch)
    with pytest.raises(ValueError, match="exceeds the committed prefix"):
        review.load_typed_backtest_financial_page(
            object(), "run-1", after_fill_sequence=121)


def test_typed_financial_review_reads_real_run_and_commit_fences(monkeypatch) -> None:
    client = MemoryClient()
    parent = {**run_row(), "mode": "backtest", "session_date": "2026-08-18"}
    publish_typed_run(client, parent)
    publish_typed_run_context(client, run_id=RUN, config=run_context(),
                              account_ids=("DU1",))
    publish_typed_batch(client, replace(batch(), status="completed"))
    insert_count = len(client.inserts)
    monkeypatch.setattr(review, "load_latest_backtest_cursor", lambda _client, _prefix: None)
    monkeypatch.setattr(review, "load_terminal_backtest_snapshot",
                        lambda _client, _prefix, *, account_id:
                        {"state_hash": "a" * 64, "account_id": account_id})
    monkeypatch.setattr(review, "load_committed_execution_page", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(review, "load_committed_commission_page", lambda *_args, **_kwargs: ())
    page = review.load_typed_backtest_financial_page(client, RUN)
    assert page["status"] == "completed"
    assert page["run"]["mode"] == "backtest"
    assert page["run"]["account_ids"] == ("DU1",)
    assert page["committed_sequence"] == 1
    assert page["complete"] is True
    assert len(client.inserts) == insert_count
