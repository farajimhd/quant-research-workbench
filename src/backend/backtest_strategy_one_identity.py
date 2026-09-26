"""SELECT-only certification of dated Strategy 1 broker identity.

The producer commits a complete market-day population, including symbols that
never become candidates.  Backtest cannot repair missing or changed identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.trading_runtime.strategy_one_identity_schema import (
    COVERAGE_TABLE, IDENTITY_TABLE,
)


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-fA-F-]{36}\Z")


@dataclass(frozen=True, slots=True)
class CertifiedIdentityPlan:
    source_build_id: str
    session_date: str
    attempt_id: str
    market_token: str
    tickers: tuple[str, ...]
    conids: tuple[int, ...]
    content_hash: str

    def conid_for(self, ticker: str) -> int:
        return self.conids[self.tickers.index(ticker)]


def identity_content_hash(rows: list[dict[str, Any]]) -> str:
    """Shared deterministic producer/reader seal over normalized scalar rows."""
    canonical = [
        [str(row[key]) for key in (
            "ticker", "symbol_id", "listing_id", "security_id",
            "ibkr_conid", "source_run_id")]
        for row in sorted(rows, key=lambda value: str(value["ticker"]))
    ]
    return sha256(json.dumps(canonical, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")).hexdigest()


def certify_identity_plan(
    market: CertifiedMarketDayPlan, *, client: Any,
) -> CertifiedIdentityPlan:
    """Require one sealed historical conid for every certified ticker."""
    if not isinstance(market, CertifiedMarketDayPlan) or len(market.sessions) != 1:
        raise ValueError("Identity certification needs one certified market session")
    build_id, session_date = market.build_id, market.sessions[0]
    if not _HEX.fullmatch(build_id) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session_date):
        raise ValueError("Market identity is not safe for a literal SQL pin")
    coverage = [json.loads(line) for line in client.execute(
        "SELECT identity_attempt_id,universe_date,ticker_count,content_hash "
        f"FROM {COVERAGE_TABLE} WHERE source_build_id='{build_id}' "
        f"AND session_date='{session_date}' FORMAT JSONEachRow").splitlines()
        if line.strip()]
    if len(coverage) != 1:
        raise RuntimeError("Strategy 1 requires one committed dated identity attempt")
    seal = coverage[0]
    attempt = str(seal.get("identity_attempt_id") or "")
    if (not _UUID.fullmatch(attempt)
            or seal.get("universe_date") != session_date
            or type(seal.get("ticker_count")) is not int
            or seal["ticker_count"] != len(market.tickers)
            or not _HEX.fullmatch(str(seal.get("content_hash") or ""))):
        raise RuntimeError("Strategy 1 dated identity coverage is invalid")
    rows = [json.loads(line) for line in client.execute(
        "SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,source_run_id "
        f"FROM {IDENTITY_TABLE} WHERE source_build_id='{build_id}' "
        f"AND session_date='{session_date}' AND identity_attempt_id='{attempt}' "
        "ORDER BY ticker FORMAT JSONEachRow").splitlines() if line.strip()]
    tickers = tuple(str(row.get("ticker") or "") for row in rows)
    if (tickers != market.tickers or len(set(tickers)) != len(tickers)
            or any(not str(row.get(field) or "")
                   for row in rows for field in (
                       "symbol_id", "listing_id", "security_id", "source_run_id"))
            or any(type(row.get("ibkr_conid")) is not int
                   or row["ibkr_conid"] <= 0 for row in rows)
            or identity_content_hash(rows) != seal["content_hash"]):
        raise RuntimeError("Strategy 1 dated identity rows differ from market seal")
    return CertifiedIdentityPlan(
        build_id, session_date, attempt, market.token, tickers,
        tuple(row["ibkr_conid"] for row in rows), seal["content_hash"])
