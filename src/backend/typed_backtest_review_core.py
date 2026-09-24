"""Process-local audited typed Backtest review core, not a saved UI route."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from threading import Lock
from time import monotonic
from typing import Any

from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, load_committed_prefix, load_typed_run_context,
)
from src.trading_runtime.arte_terminal_recovery_audit import audit_terminal_backtest_recovery
from src.trading_runtime.journal_contract import canonical_json


class AuditedSessionCache:
    """Bounded LRU of small audit attestations, never durable authority."""

    def __init__(self, *, max_sessions: int = 8, max_bytes: int = 32 * 1024 * 1024,
                 max_entry_bytes: int = 4 * 1024 * 1024,
                 ttl_seconds: float = 300.0) -> None:
        if (type(max_sessions) is not int or max_sessions < 1
                or type(max_bytes) is not int or max_bytes < 1
                or type(max_entry_bytes) is not int or max_entry_bytes < 1
                or max_entry_bytes > max_bytes or ttl_seconds <= 0):
            raise ValueError("Typed review cache limits are invalid")
        self.max_sessions = max_sessions
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[tuple[str, str, str], tuple[dict[str, Any], int, float]] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def candidate_keys(self, scope: str, run_id: str) -> tuple[tuple[str, str, str], ...]:
        with self._lock:
            self._expire()
            return tuple(key for key in self._items if key[:2] == (scope, run_id))

    def _expire(self) -> None:
        now = monotonic()
        for key, (_value, size, expires) in tuple(self._items.items()):
            if expires <= now:
                del self._items[key]
                self._bytes -= size

    def get(self, key: tuple[str, str, str]) -> dict[str, Any] | None:
        with self._lock:
            self._expire()
            entry = self._items.get(key)
            if entry is None:
                return None
            self._items.move_to_end(key)
            return deepcopy(entry[0])

    def put(self, key: tuple[str, str, str], attestation: dict[str, Any]) -> None:
        size = len(canonical_json(attestation).encode("utf-8"))
        if size > self.max_entry_bytes:
            raise ValueError("Typed Backtest audit exceeds session cache budget")
        with self._lock:
            self._expire()
            prior = self._items.pop(key, None)
            if prior is not None:
                self._bytes -= prior[1]
            self._items[key] = (deepcopy(attestation), size, monotonic() + self.ttl_seconds)
            self._bytes += size
            while len(self._items) > self.max_sessions or self._bytes > self.max_bytes:
                _old_key, (_old_accounts, old_size, _expires) = self._items.popitem(last=False)
                self._bytes -= old_size


_SESSION_CACHE = AuditedSessionCache()


def _client_scope(client: Any) -> str:
    """Never share an audit across endpoints or credential identities."""
    base = getattr(client, "base_url", None)
    user = getattr(client, "user", None)
    password = getattr(client, "password", None)
    if all(isinstance(value, str) and value for value in (base, user, password)):
        return sha256(f"{base}\x00{user}\x00{password}".encode()).hexdigest()
    raise ValueError("Typed Backtest review cache requires a stable client authority identity")


def _head_matches(client: Any, run_id: str, prefix: Any) -> bool:
    """Cheap terminal head check; full chain was verified on audit cache miss."""
    rows = _rows(client,
        "SELECT run_id,batch_id,last_sequence,status,source_cursor "
        "FROM arte.trading_commit_v1 "
        f"WHERE run_id={_literal(run_id)} "
        "ORDER BY last_sequence DESC,batch_id DESC LIMIT 2 FORMAT JSONEachRow")
    if not rows or len(rows) > 2:
        return False
    head = rows[0]
    return (str(head.get("run_id")) == run_id
            and str(head.get("batch_id")) == prefix.last_batch_id
            and int(head.get("last_sequence", -1)) == prefix.last_sequence
            and str(head.get("status")) == prefix.status
            and str(head.get("source_cursor")) == prefix.source_cursor
            and (len(rows) == 1 or int(rows[1].get("last_sequence", -1)) < prefix.last_sequence))


def _cache_key(client: Any, run_id: str, context: dict[str, Any], prefix: Any) -> tuple[str, str, str]:
    digest = sha256(canonical_json({"context": context, "prefix": prefix}).encode()).hexdigest()
    return _client_scope(client), run_id, digest


def load_typed_backtest_review_core(
    client: Any, run_id: str, *, after_sequence: int = 0, limit: int = 500,
    cache: AuditedSessionCache | None = None,
) -> dict[str, Any]:
    """Return a bounded committed event page after whole-run recovery audit.

    The page exposes only the typed envelope and its one-to-one typed detail.
    Child evidence families and saved-review/controller projections are not
    reconstructed here; callers must not label this a full strategy activity
    or resumable run view. A cache miss or process restart performs the full
    audit. Hits revalidate the pinned context and terminal commit head before
    and after a page; the bounded TTL relies on immutable append-only terminal
    rows. A head query cannot detect a later historical duplicate insert.
    """
    if (not isinstance(run_id, str) or not run_id
            or type(after_sequence) is not int or after_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or (cache is not None and not isinstance(cache, AuditedSessionCache))):
        raise ValueError("Typed Backtest review core has invalid page bounds")
    selected_cache = cache if cache is not None else _SESSION_CACHE
    context = load_typed_run_context(client, run_id)
    if context.get("mode") != "backtest":
        raise ValueError("Typed Backtest review core requires a terminal committed run")
    # Context is small; the full commit chain is read only on cache miss.
    key_prefix = None
    account_attestation = None
    for key in selected_cache.candidate_keys(_client_scope(client), run_id):
        candidate = selected_cache.get(key)
        if candidate is not None:
            candidate_prefix = candidate["prefix"]
            candidate_context = candidate["context"]
            if (candidate_context == context
                    and _head_matches(client, run_id, candidate_prefix)):
                key_prefix = candidate_prefix
                account_attestation = candidate["account_attestation"]
                break
    if account_attestation is None:
        prefix = load_committed_prefix(client, run_id)
        if prefix is None or prefix.status not in {"completed", "stopped", "failed"}:
            raise ValueError("Typed Backtest review core requires a terminal committed run")
    else:
        prefix = key_prefix
    if after_sequence > prefix.last_sequence:
        raise ValueError("Typed Backtest review cursor exceeds committed prefix")
    if account_attestation is None:
        accounts = audit_terminal_backtest_recovery(client, run_id)
        if (load_typed_run_context(client, run_id) != context
                or not _head_matches(client, run_id, prefix)
                or set(accounts) != set(context["account_ids"])):
            raise RuntimeError("Typed Backtest review authority changed during audit")
        account_attestation = {
            "count": len(accounts),
            "hash": sha256(canonical_json(tuple(
                (account_id, sha256(canonical_json(accounts[account_id]).encode()).hexdigest())
                for account_id in sorted(accounts)
            )).encode()).hexdigest(),
        }
        selected_cache.put(_cache_key(client, run_id, context, prefix),
                           {"account_attestation": account_attestation,
                            "prefix": prefix, "context": context})
    page = load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit,
    )
    next_sequence = int(page[-1].event["sequence"]) if page else after_sequence
    if (load_typed_run_context(client, run_id) != context
            or not _head_matches(client, run_id, prefix)):
        raise RuntimeError("Typed Backtest review authority changed during page read")
    return {
        "schema_version": "typed-backtest-review-core-v1",
        "review_only": True, "full_saved_review": False,
        "run": context, "status": prefix.status,
        "committed_sequence": prefix.last_sequence,
        "account_snapshot_count": account_attestation["count"],
        "account_snapshot_hash": account_attestation["hash"],
        "events": tuple({
            "event": row.event, "detail_family": row.detail_family,
            "detail": row.detail,
        } for row in page),
        "next_sequence": next_sequence,
        "complete": next_sequence == prefix.last_sequence,
    }
