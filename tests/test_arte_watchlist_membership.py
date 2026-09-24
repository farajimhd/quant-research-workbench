from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re

import pytest

from src.trading_runtime.arte_watchlist_membership import (
    COVERAGE_DDL, COVERAGE_TABLE, INTERVAL_DDL, INTERVAL_TABLE,
    load_watchlist_membership_as_of,
)
from src.trading_runtime import arte_watchlist_membership as membership
from src.trading_runtime.journal_contract import canonical_json


HASH = "a" * 64
AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)
REQUEST = dict(session_date=date(2026, 8, 18), watchlist_id="watch-1",
               configuration_hash=HASH, source_revision="scanner-v2", as_of=AT)


def _row(**values):
    return {**values, "content_hash": sha256(canonical_json(values).encode()).hexdigest()}


def _coverage(intervals=None):
    intervals = intervals if intervals is not None else [_interval()]
    ordered = sorted((row["ticker"], row["effective_at"], row["source_event_id"],
                      row["content_hash"]) for row in intervals)
    return _row(session_date="2026-08-18", watchlist_id="watch-1",
                configuration_hash=HASH, source_revision="scanner-v2",
                source_cursor="scanner:2026-08-18:complete",
                interval_count=len(intervals),
                interval_hash=sha256(canonical_json(ordered).encode()).hexdigest(),
                coverage_start="2026-08-18T13:00:00.000000+00:00",
                complete_through="2026-08-18T15:00:00.000000+00:00",
                certified_at="2026-08-18T15:01:00.000000+00:00")


def _interval(*, ticker="AAA", start="2026-08-18T13:30:00.000000+00:00",
              expires=None, end=None, event_id="event-1"):
    return _row(session_date="2026-08-18", watchlist_id="watch-1",
                configuration_hash=HASH, source_revision="scanner-v2", ticker=ticker,
                effective_at=start, available_at=start, expires_at=expires,
                ended_at=end, source_event_id=event_id, reason="rules passed")


class Client:
    def __init__(self):
        self.rows = {COVERAGE_TABLE: [_coverage()], INTERVAL_TABLE: [_interval()]}
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        assert sql.startswith("SELECT * FROM arte.")
        name = sql.split("arte.", 1)[1].split(" ", 1)[0]
        rows = list(self.rows[name])
        if name == INTERVAL_TABLE:
            rows.sort(key=lambda row: (row["ticker"], row["effective_at"], row["source_event_id"]))
            match = re.search(r"AND \(ticker,effective_at,source_event_id\) >= \('([^']+)',toDateTime64\('([^']+)',6,'UTC'\),'([^']+)'\)", sql)
            if match:
                ticker, clock, event_id = match.groups()
                key = (ticker, clock.replace(" ", "T") + "+00:00", event_id)
                rows = [row for row in rows if
                        (row["ticker"], row["effective_at"], row["source_event_id"]) >= key]
        return "\n".join(json.dumps(row) for row in rows[:int(re.search(r"LIMIT (\d+)", sql).group(1))])


def test_as_of_reads_only_certified_typed_intervals() -> None:
    client = Client()
    members = load_watchlist_membership_as_of(client, **REQUEST)
    assert [member.ticker for member in members] == ["AAA"]
    assert any("ORDER BY ticker,effective_at,source_event_id" in sql for sql in client.queries)
    assert "live_market_ssd" in INTERVAL_DDL and "live_market_ssd" in COVERAGE_DDL
    assert "PARTITION BY toYYYYMM(session_date)" in INTERVAL_DDL
    assert "PARTITION BY toYYYYMM(session_date)" in COVERAGE_DDL
    assert "JSON" not in INTERVAL_DDL and "JSON" not in COVERAGE_DDL


def test_missing_coverage_and_expiry_fail_closed() -> None:
    client = Client()
    client.rows[COVERAGE_TABLE].clear()
    with pytest.raises(RuntimeError, match="coverage watermark"):
        load_watchlist_membership_as_of(client, **REQUEST)
    client.rows[COVERAGE_TABLE] = [_coverage()]
    client.rows[INTERVAL_TABLE] = [_interval(expires="2026-08-18T13:59:00.000000+00:00")]
    client.rows[COVERAGE_TABLE] = [_coverage(client.rows[INTERVAL_TABLE])]
    assert load_watchlist_membership_as_of(client, **REQUEST) == ()


def test_overlap_duplicate_and_uncertified_scope_fail_closed() -> None:
    client = Client()
    client.rows[INTERVAL_TABLE].append(_interval(start="2026-08-18T13:45:00.000000+00:00",
                                                 event_id="event-2"))
    client.rows[COVERAGE_TABLE] = [_coverage(client.rows[INTERVAL_TABLE])]
    with pytest.raises(RuntimeError, match="intervals overlap"):
        load_watchlist_membership_as_of(client, **REQUEST)
    client.rows[INTERVAL_TABLE] = [_interval(), _interval()]
    client.rows[COVERAGE_TABLE] = [_coverage(client.rows[INTERVAL_TABLE])]
    with pytest.raises(RuntimeError, match="duplicated or unordered"):
        load_watchlist_membership_as_of(client, **REQUEST)
    client.rows[INTERVAL_TABLE] = [_interval()]
    client.rows[COVERAGE_TABLE] = [_coverage(), _coverage()]
    with pytest.raises(RuntimeError, match="coverage watermark"):
        load_watchlist_membership_as_of(client, **REQUEST)


def test_changed_content_and_uncovered_time_fail_closed() -> None:
    client = Client()
    client.rows[INTERVAL_TABLE][0]["ticker"] = "BBB"
    with pytest.raises(RuntimeError, match="content hash"):
        load_watchlist_membership_as_of(client, **REQUEST)
    client.rows[INTERVAL_TABLE] = [_interval()]
    with pytest.raises(RuntimeError, match="outside certified coverage"):
        load_watchlist_membership_as_of(client, **{**REQUEST, "as_of": datetime(
            2026, 8, 18, 16, tzinfo=timezone.utc)})


def test_keyset_pagination_and_coverage_detect_missing_interval(monkeypatch) -> None:
    monkeypatch.setattr(membership, "PAGE_SIZE", 2)
    client = Client()
    client.rows[INTERVAL_TABLE] = [_interval(ticker=ticker, event_id=f"event-{ticker}")
                                   for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    client.rows[COVERAGE_TABLE] = [_coverage(client.rows[INTERVAL_TABLE])]
    assert [row.ticker for row in load_watchlist_membership_as_of(client, **REQUEST)] == [
        "AAA", "BBB", "CCC", "DDD", "EEE"]
    assert len([sql for sql in client.queries if "ORDER BY ticker" in sql]) == 3
    client.rows[INTERVAL_TABLE].pop()
    with pytest.raises(RuntimeError, match="coverage differs"):
        load_watchlist_membership_as_of(client, **REQUEST)


def test_keyset_boundary_duplicate_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(membership, "PAGE_SIZE", 2)
    client = Client()
    client.rows[INTERVAL_TABLE] = [_interval(ticker=ticker, event_id=f"event-{ticker}")
                                   for ticker in ("AAA", "BBB", "CCC")]
    client.rows[INTERVAL_TABLE].append(dict(client.rows[INTERVAL_TABLE][1]))
    client.rows[COVERAGE_TABLE] = [_coverage(client.rows[INTERVAL_TABLE])]
    with pytest.raises(RuntimeError, match="duplicated or unordered"):
        load_watchlist_membership_as_of(client, **REQUEST)
