"""Pure per-Watchlist reducer for future certified persisted-product scans.

No query or QMD materializer is hidden here. A caller must certify that each
snapshot covers every ticker in the pinned fixed-market plan at a completed
bar boundary before this reducer may be used for execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")


@dataclass(frozen=True, slots=True)
class MembershipCandidate:
    ticker: str
    rank: int
    score: Decimal
    reason: str

    def __post_init__(self) -> None:
        if (_TICKER.fullmatch(self.ticker) is None
                or type(self.rank) is not int or self.rank < 1
                or type(self.score) is not Decimal or not self.score.is_finite()
                or not self.reason):
            raise ValueError("Fixed Watchlist candidate lacks typed rank evidence")


@dataclass(frozen=True, slots=True)
class CompletedMembership:
    watchlist_id: str
    plan_hash: str
    market_plan_token: str
    query_sha256: str
    universe_fingerprint: str
    evaluated_tickers: tuple[str, ...]
    completed_at: datetime
    candidates: tuple[MembershipCandidate, ...]

    def __post_init__(self) -> None:
        if (not self.watchlist_id or _HEX.fullmatch(self.plan_hash) is None
                or _HEX.fullmatch(self.market_plan_token) is None
                or _HEX.fullmatch(self.query_sha256) is None
                or _HEX.fullmatch(self.universe_fingerprint) is None
                or not self.evaluated_tickers
                or tuple(sorted(set(self.evaluated_tickers))) != self.evaluated_tickers
                or any(_TICKER.fullmatch(ticker) is None
                       for ticker in self.evaluated_tickers)
                or self.universe_fingerprint != universe_fingerprint(self.evaluated_tickers)
                or self.completed_at.tzinfo is None
                or self.completed_at.astimezone(timezone.utc).microsecond % 100_000
                or any(not isinstance(row, MembershipCandidate)
                       for row in self.candidates)
                or len({row.ticker for row in self.candidates}) != len(self.candidates)
                or any(row.ticker not in self.evaluated_tickers
                       for row in self.candidates)
                or len({row.rank for row in self.candidates}) != len(self.candidates)
                or tuple(sorted(self.candidates, key=lambda row: row.rank)) != self.candidates):
            raise ValueError("Fixed Watchlist snapshot is not one complete typed boundary")


@dataclass(frozen=True, slots=True)
class MembershipTransition:
    watchlist_id: str
    ticker: str
    event: str
    effective_at: datetime
    rank: int | None
    score: Decimal | None
    reason: str
    plan_hash: str
    market_plan_token: str
    query_sha256: str


def reduce_completed_memberships(
    snapshots: tuple[CompletedMembership, ...],
    *, expected_tickers: tuple[str, ...],
) -> tuple[MembershipTransition, ...]:
    """Emit exact per-Watchlist adds, removals, and rank/score changes."""
    if (not expected_tickers
            or tuple(sorted(set(expected_tickers))) != expected_tickers
            or any(_TICKER.fullmatch(ticker) is None for ticker in expected_tickers)):
        raise ValueError("Fixed Watchlist requires a pinned full universe")
    expected_fingerprint = universe_fingerprint(expected_tickers)
    previous: dict[str, CompletedMembership] = {}
    transitions: list[MembershipTransition] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, CompletedMembership):
            raise ValueError("Watchlist reducer requires certified typed snapshots")
        if (snapshot.evaluated_tickers != expected_tickers
                or snapshot.universe_fingerprint != expected_fingerprint):
            raise ValueError("Fixed Watchlist evaluated universe is incomplete")
        prior = previous.get(snapshot.watchlist_id)
        if prior is not None and (
            prior.plan_hash != snapshot.plan_hash
            or prior.market_plan_token != snapshot.market_plan_token
            or prior.query_sha256 != snapshot.query_sha256
            or prior.completed_at >= snapshot.completed_at
        ):
            raise ValueError("Fixed Watchlist authority or completed clock changed")
        before = {row.ticker: row for row in prior.candidates} if prior else {}
        after = {row.ticker: row for row in snapshot.candidates}
        for ticker in sorted(before.keys() | after.keys()):
            old, new = before.get(ticker), after.get(ticker)
            if old == new:
                continue
            event = "removed" if new is None else (
                "added" if old is None else "rank_changed")
            transitions.append(MembershipTransition(
                snapshot.watchlist_id, ticker, event, snapshot.completed_at,
                new.rank if new else None, new.score if new else None,
                new.reason if new else old.reason,
                snapshot.plan_hash, snapshot.market_plan_token,
                snapshot.query_sha256,
            ))
        previous[snapshot.watchlist_id] = snapshot
    return tuple(transitions)


def universe_fingerprint(tickers: tuple[str, ...]) -> str:
    """Deterministic identity of every pinned symbol, not just matching rows."""
    return sha256("\n".join(tickers).encode("ascii")).hexdigest()
