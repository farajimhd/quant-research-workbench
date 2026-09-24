"""Persistent two-phase fence for one typed Portfolio admission.

The prepared row is written before either journal unit. Recovery rejects a
prepared row lacking its committed mate; it must not treat a partially written
event batch or snapshot as an admitted reservation. All rows are scalar and
use the journal-only ClickHouse principal.
"""
from __future__ import annotations

from datetime import timezone
from hashlib import sha256
import re
from typing import Any, Mapping

from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, _canonical_typed_content, _insert, _literal, _rows,
    publish_typed_batch, typed_row,
)
from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, prepare_captured_portfolio_snapshot,
    publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.journal_contract import canonical_json


_TABLE = "trading_admission_fence_v1"


def _fence(batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
           phase: str, snapshot_hash: str | None = None) -> dict[str, Any]:
    if phase not in {"prepared", "committed"} or (phase == "committed") != (snapshot_hash is not None):
        raise ValueError("Admission fence phase and snapshot hash disagree")
    if snapshot_hash is not None and re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None:
        raise ValueError("Admission snapshot hash must be lowercase SHA-256 hex")
    return typed_row(_TABLE, {
        "run_id": batch.run_id,
        "admission_month": captured.snapshot_at.astimezone(timezone.utc).date().replace(day=1).isoformat(),
        "account_id": captured.account_id,
        "state_revision": captured.state_revision,
        "attempt_id": batch.attempt_id,
        "batch_id": batch.batch_id,
        "first_sequence": batch.first_sequence,
        "last_sequence": batch.last_sequence,
        "phase": phase,
        "snapshot_hash": snapshot_hash,
        "captured_at": captured.snapshot_at.astimezone(timezone.utc).isoformat(),
    })


def _canonical_stored(row: Mapping[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(_TABLE, content, stored_utc=True)
    digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    if digest != str(row["content_hash"]):
        raise RuntimeError("Admission fence content differs from its hash")
    return canonical


def _stored(client: Any, run_id: str, account_id: str,
            state_revision: int) -> dict[str, dict[str, Any]]:
    rows = _rows(client,
        "SELECT * FROM arte.trading_admission_fence_v1 "
        f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
        f"AND state_revision={int(state_revision)} FORMAT JSONEachRow")
    result = {}
    for row in rows:
        canonical = _canonical_stored(row)
        phase = canonical["phase"]
        if phase not in {"prepared", "committed"} or phase in result:
            raise RuntimeError("Admission fence has an invalid or duplicate phase")
        result[phase] = canonical
    return result


def _publish_phase(client: Any, row: Mapping[str, Any]) -> None:
    run_id, account_id = str(row["run_id"]), str(row["account_id"])
    revision, phase = int(row["state_revision"]), str(row["phase"])
    expected = _canonical_typed_content(
        _TABLE, {key: value for key, value in row.items() if key != "content_hash"})
    actual = _stored(client, run_id, account_id, revision).get(phase)
    if actual is not None:
        if actual != expected:
            raise RuntimeError("Admission fence revision has conflicting content")
        return
    _insert(client, _TABLE, (row,),
            f"admission:{run_id}:{account_id}:{revision}:{phase}")
    if _stored(client, run_id, account_id, revision).get(phase) != expected:
        raise RuntimeError("Admission fence did not become durable")


def _verify_committed_sources(client: Any, row: Mapping[str, Any]) -> None:
    run_id, account_id = str(row["run_id"]), str(row["account_id"])
    revision = int(row["state_revision"])
    events = _rows(client,
        "SELECT attempt_id,first_sequence,last_sequence FROM arte.trading_commit_v1 "
        f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(str(row['batch_id']))}) "
        "FORMAT JSONEachRow")
    snapshots = _rows(client,
        "SELECT state_hash FROM arte.trading_portfolio_snapshot_commit_v1 "
        f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
        f"AND state_revision={revision} FORMAT JSONEachRow")
    if (len(events) != 1 or len(snapshots) != 1
            or str(events[0]["attempt_id"]) != str(row["attempt_id"])
            or int(events[0]["first_sequence"]) != int(row["first_sequence"])
            or int(events[0]["last_sequence"]) != int(row["last_sequence"])
            or str(snapshots[0]["state_hash"]) != str(row["snapshot_hash"])):
        raise RuntimeError("Admission fence references missing or conflicting commits")


def load_fenced_admission(client: Any, *, run_id: str, account_id: str,
                          state_revision: int) -> dict[str, Any] | None:
    """Cold-read one complete admission; reject a prepared-only revision."""
    phases = _stored(client, run_id, account_id, state_revision)
    if not phases:
        return None
    if set(phases) != {"prepared", "committed"}:
        raise RuntimeError("Admission is prepared but not committed; reconcile before trading")
    prepared, committed = phases["prepared"], phases["committed"]
    if (prepared["snapshot_hash"] is not None
            or committed["snapshot_hash"] is None
            or any(prepared[key] != committed[key] for key in prepared
                   if key not in {"phase", "snapshot_hash"})):
        raise RuntimeError("Admission prepared and committed identities differ")
    _verify_committed_sources(client, committed)
    return committed


def publish_fenced_admission(client: Any, batch: TypedJournalBatch,
                              captured: CapturedPortfolioSnapshot) -> str:
    """Worker-only publication, with a persistent late commit fence."""
    prepared = _fence(batch, captured, "prepared")
    _publish_phase(client, prepared)
    if publish_typed_batch(client, batch) != batch.batch_id:
        raise RuntimeError("Admission event batch returned a different identity")
    snapshot = prepare_captured_portfolio_snapshot(captured)
    snapshot_hash = publish_prepared_portfolio_snapshot(client, snapshot)
    committed = _fence(batch, captured, "committed", snapshot_hash)
    _publish_phase(client, committed)
    loaded = load_fenced_admission(
        client, run_id=batch.run_id, account_id=captured.account_id,
        state_revision=captured.state_revision)
    if loaded is None or loaded["snapshot_hash"] != snapshot_hash:
        raise RuntimeError("Admission commit fence did not become durable")
    return snapshot_hash


def verify_no_incomplete_admissions(client: Any, run_id: str) -> None:
    """Fail startup if any prepared admission lacks exactly one commit mate."""
    if not run_id:
        raise ValueError("Admission run identity is required")
    rows = _rows(client,
        "SELECT account_id,state_revision,countIf(phase='prepared') AS prepared_count,"
        "countIf(phase='committed') AS committed_count,count() AS total_count "
        "FROM arte.trading_admission_fence_v1 "
        f"WHERE run_id={_literal(run_id)} GROUP BY account_id,state_revision "
        "HAVING prepared_count!=1 OR committed_count!=1 OR total_count!=2 "
        "LIMIT 1 FORMAT JSONEachRow")
    if rows:
        raise RuntimeError("Run has an incomplete or duplicate admission fence")
