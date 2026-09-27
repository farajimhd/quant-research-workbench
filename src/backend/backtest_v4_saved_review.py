"""Cold-verified, disk-free terminal evidence for immutable Strategy 1.

This is a bounded normalized-journal page, not a fabricated legacy Canvas
controller or a resumable execution state. JSON is only the API transport.
"""
from __future__ import annotations

from uuid import UUID

from src.trading_runtime.arte_backtest_snapshot_anchor import (
    load_terminal_backtest_snapshot,
)
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import load_typed_run_context
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.backend.typed_backtest_review_core import (
    AuditedSessionCache, _cache_key, _client_scope, _head_matches,
)


_V4_CACHE = AuditedSessionCache(max_sessions=8, max_bytes=8 * 1024 * 1024,
                                max_entry_bytes=512 * 1024, ttl_seconds=300)


def load_v4_terminal_review_page(client, run_id: str, *,
                                 after_sequence: int = 0,
                                 limit: int = 250,
                                 cache: AuditedSessionCache | None = None) -> dict:
    """Cold-audit once; recheck the terminal head before each bounded page."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 review requires a UUID run id") from exc
    if (type(after_sequence) is not int or after_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or cache is not None and not isinstance(cache, AuditedSessionCache)):
        raise ValueError("Strategy 1 review page bounds are invalid")
    selected_cache = cache if cache is not None else _V4_CACHE
    context = load_typed_run_context(client, normalized)
    if (context["mode"] != "backtest"
            or context["strategy_id"] != STRATEGY_ID
            or int(context["strategy_revision"]) != STRATEGY_NUMBER
            or context["evaluation_interval_ms"] != 100):
        raise ValueError("Saved review accepts only immutable Strategy 1 at 100 ms")
    attestation = None
    for key in selected_cache.candidate_keys(_client_scope(client), normalized):
        candidate = selected_cache.get(key)
        if (candidate is not None and candidate["context"] == context
                and _head_matches(client, normalized, candidate["prefix"])):
            attestation = candidate
            break
    if attestation is None:
        prefix = load_verified_v4_prefix(client, normalized)
        if prefix is None or prefix.status not in {"completed", "stopped", "failed"}:
            raise ValueError("Saved review requires a cold-verified terminal V4 run")
        accounts = {
            account_id: load_terminal_backtest_snapshot(
                client, prefix, account_id=account_id)
            for account_id in context["account_ids"]
        }
        cursor = load_latest_backtest_cursor(client, prefix)
        if (cursor is None and prefix.source_cursor != "start"
                or cursor is not None and (
                    str(cursor["session_date"]) != str(context["session_date"])
                    or prefix.source_cursor !=
                    f"{cursor['session_date']}:{int(cursor['boundary_ms'])}")):
            raise RuntimeError("Saved review market cursor differs from terminal run")
        if not _head_matches(client, normalized, prefix):
            raise RuntimeError("Saved review terminal head changed during audit")
        attestation = {
            "context": context, "prefix": prefix, "cursor": cursor,
            "accounts": {
                account_id: {
                    "state_hash": snapshot["state_hash"],
                    "state_revision": snapshot["state_revision"],
                    "snapshot_at": snapshot["snapshot_at"],
                }
                for account_id, snapshot in accounts.items()
            },
        }
        selected_cache.put(_cache_key(client, normalized, context, prefix),
                           attestation)
    prefix = attestation["prefix"]
    cursor = attestation["cursor"]
    if after_sequence > prefix.last_sequence:
        raise ValueError("Saved review cursor exceeds the verified journal")
    page = load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved review terminal head changed during page read")
    next_sequence = int(page[-1].event["sequence"]) if page else after_sequence
    return {
        "schema_version": "strategy-one-v4-terminal-review-page-v1",
        "run": context,
        "status": prefix.status,
        "verified_sequence": prefix.last_sequence,
        "market_cursor": cursor,
        "market_cursor_verified": cursor is not None,
        "limitations": (["This archived V4 run has no persisted market-boundary cursor; "
                         "its exact processed-through clock is unavailable."]
                        if cursor is None else []),
        "accounts": attestation["accounts"],
        "events": tuple({
            "event": row.event,
            "detail_family": row.detail_family,
            "detail": row.detail,
        } for row in page),
        "next_sequence": next_sequence,
        "complete": next_sequence == prefix.last_sequence,
        "resume_supported": False,
    }
