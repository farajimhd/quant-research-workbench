"""Narrow, normalized Strategy 1 commit authority (pre-publication contract).

The writer must verify every typed detail family before publishing these rows.
No runtime may treat either row alone as a durable fence: recovery requires the
unique commit, its complete child-family set, and the detail-row readback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re
from typing import Mapping, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE


@dataclass(frozen=True, slots=True)
class V4CommittedPrefix:
    """Cold-verified normalized run chain, not an admission or write lease."""

    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


def load_verified_v4_prefix(client, run_id: str, *,
                            max_commits: int = 100_000) -> V4CommittedPrefix | None:
    """Recompute every detail seal and require one complete contiguous chain.

    This SELECT-only cold path is intentionally outside the execution loop.
    It never treats an unfenced detail row as recovery authority.
    """
    from src.trading_runtime.arte_journal_writer import _CONTRACTS, _literal, _rows

    if (not isinstance(run_id, str) or not run_id
            or type(max_commits) is not int or not 1 <= max_commits <= 100_000):
        raise ValueError("V4 recovery needs a bounded run identity")
    columns = ",".join(name for name, _ in
                       _CONTRACTS["trading_commit_v4"].columns)
    commits = _rows(client,
        f"SELECT {columns} FROM arte.trading_commit_v4 "
        f"WHERE run_id={_literal(run_id)} "
        "ORDER BY first_sequence,batch_id "
        f"LIMIT {max_commits + 1} FORMAT JSONEachRow")
    if len(commits) > max_commits:
        raise RuntimeError("V4 recovery commit count exceeds its memory bound")
    if not commits:
        return None
    prior_id = str(UUID(int=0))
    last_sequence = 0
    status = "running"
    run_month = commits[0]["run_month"]
    batch_ids: list[str] = []
    seen_ids: set[str] = set()
    for row in commits:
        batch_id = str(UUID(str(row["batch_id"])))
        cursor = row["source_cursor"]
        if (row["run_id"] != run_id or row["run_month"] != run_month
                or str(UUID(str(row["prior_batch_id"]))) != prior_id
                or row["first_sequence"] != last_sequence + 1
                or row["last_sequence"] < row["first_sequence"]
                or row["event_count"] !=
                   row["last_sequence"] - row["first_sequence"] + 1
                or status != "running"
                or row["status"] not in {"running", "completed", "stopped", "failed"}
                or not isinstance(cursor, str) or not cursor
                or cursor.lstrip("\ufeff \t\r\n").startswith(("{", "["))
                or batch_id in seen_ids):
            raise RuntimeError("V4 committed run chain is forked or not contiguous")
        verified, _ = load_verified_commit_v4(
            client, run_id=run_id, batch_id=batch_id)
        if verified != row:
            raise RuntimeError("V4 cold commit differs from ordered run inventory")
        prior_id = batch_id
        last_sequence = row["last_sequence"]
        status = row["status"]
        batch_ids.append(batch_id)
        seen_ids.add(batch_id)
    return V4CommittedPrefix(
        run_id, last_sequence, prior_id, commits[-1]["source_cursor"],
        status, tuple(batch_ids))


def _family_set_hash(rows: Sequence[Mapping]) -> str:
    return sha256(canonical_json(sorted(
        (row["family_name"], row["row_count"], row["row_hash"])
        for row in rows
    )).encode()).hexdigest()


def prepare_commit_v4(
    *, run_id: str, run_month, attempt_id: str, batch_id: str,
    prior_batch_id: str, first_sequence: int, last_sequence: int,
    source_cursor: str, status: str,
    sealed_families: Sequence[tuple[str, Sequence[Mapping]]],
    committed_at: datetime,
) -> tuple[dict, tuple[dict, ...]]:
    """Describe one complete commit without inserting or accepting opaque data."""
    if (not run_id or run_month.day != 1 or not source_cursor
            or status not in {"running", "completed", "stopped", "failed"}
            or type(first_sequence) is not int or first_sequence < 1
            or type(last_sequence) is not int or last_sequence < first_sequence
            or committed_at.tzinfo is None):
        raise ValueError("V4 commit identity or completed cursor is invalid")
    for identity in (attempt_id, batch_id, prior_batch_id):
        UUID(identity)
    family_rows = []
    seen = set()
    event_count = last_sequence - first_sequence + 1
    for name, rows in sealed_families:
        if (name in seen or not re.fullmatch(r"trading_[a-z0-9_]+_v\d+", name)):
            raise ValueError("V4 commit has duplicate or invalid family identity")
        seen.add(name)
        if not rows:
            continue
        identities = []
        for row in rows:
            record_id = str(UUID(str(row["record_id"])))
            content_hash = str(row["content_hash"])
            if (row["run_id"] != run_id or str(UUID(str(row["batch_id"]))) != batch_id
                    or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None):
                raise ValueError("V4 family row differs from its batch authority")
            identities.append((record_id, content_hash))
        if len({record_id for record_id, _ in identities}) != len(identities):
            raise ValueError("V4 family repeated a typed row identity")
        family_rows.append({
            "run_id": run_id, "run_month": run_month.isoformat(),
            "batch_id": batch_id, "family_name": name,
            "row_count": len(rows),
            "row_hash": sha256(canonical_json(sorted(identities)).encode()).hexdigest(),
        })
        if name == "trading_event_v1" and len(rows) != event_count:
            raise ValueError("V4 event family differs from the sequence span")
    if ("trading_event_v1" not in {row["family_name"] for row in family_rows}
            or len(family_rows) > 65_535):
        raise ValueError("V4 commit requires one nonempty event family")
    family_rows.sort(key=lambda row: row["family_name"])
    family_set_hash = _family_set_hash(family_rows)
    commit = {
        "run_id": run_id, "run_month": run_month.isoformat(),
        "attempt_id": attempt_id, "batch_id": batch_id,
        "prior_batch_id": prior_batch_id,
        "first_sequence": first_sequence, "last_sequence": last_sequence,
        "event_count": event_count, "family_count": len(family_rows),
        "family_set_hash": family_set_hash,
        "source_cursor": source_cursor, "status": status,
        "committed_at": committed_at.astimezone(timezone.utc).isoformat(),
    }
    commit["content_hash"] = sha256(canonical_json({
        key: value for key, value in commit.items() if key != "committed_at"
    }).encode()).hexdigest()
    return commit, tuple(family_rows)


def verify_commit_v4(
    commit: Mapping, family_rows: Sequence[Mapping],
    detail_identities: Mapping[str, Sequence[tuple[str, str]]],
) -> None:
    """Verify complete family identities after every detail row hash is checked.

    `detail_identities` must come from a bounded readback that has independently
    recomputed each typed row's content hash; identity seals alone cannot prove
    that a row's non-key scalar columns are intact.
    """
    try:
        run_id = str(commit["run_id"])
        batch_id = str(UUID(str(commit["batch_id"])))
        month = str(commit["run_month"])
        count = int(commit["family_count"])
        events = int(commit["event_count"])
        span = int(commit["last_sequence"]) - int(commit["first_sequence"]) + 1
        if (not run_id or count < 1 or events < 1 or events != span
                or count != len(family_rows)):
            raise ValueError("V4 commit count or sequence span differs")
        content = {key: value for key, value in commit.items()
                   if key not in {"committed_at", "content_hash"}}
        if sha256(canonical_json(content).encode()).hexdigest() != commit["content_hash"]:
            raise ValueError("V4 commit scalar content differs from its seal")
        names = []
        for row in family_rows:
            name = str(row["family_name"])
            if (not re.fullmatch(r"trading_[a-z0-9_]+_v\d+", name)
                    or row["run_id"] != run_id
                    or str(UUID(str(row["batch_id"]))) != batch_id
                    or str(row["run_month"]) != month
                    or type(row["row_count"]) is not int
                    or row["row_count"] < 1
                    or re.fullmatch(r"[0-9a-f]{64}", str(row["row_hash"])) is None):
                raise ValueError("V4 family row differs from its commit")
            names.append(name)
            identities = detail_identities[name]
            normalized = [(str(UUID(str(record_id))), str(digest))
                          for record_id, digest in identities]
            if (len(normalized) != row["row_count"]
                    or len({record_id for record_id, _ in normalized}) != len(normalized)
                    or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                           for _, digest in normalized)
                    or sha256(canonical_json(sorted(normalized)).encode()).hexdigest()
                    != row["row_hash"]):
                raise ValueError("V4 detail identities differ from family seal")
        if (len(set(names)) != count
                or set(detail_identities) != set(names)
                or "trading_event_v1" not in names
                or next(row["row_count"] for row in family_rows
                        if row["family_name"] == "trading_event_v1") != events
                or _family_set_hash(family_rows) != commit["family_set_hash"]):
            raise ValueError("V4 family set differs from commit seal")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("V4 readback lacks complete normalized families") from exc


def load_verified_commit_v4(
    client, *, run_id: str, batch_id: str,
    max_rows_per_family: int = 65_536,
) -> tuple[dict, tuple[dict, ...]]:
    """SELECT one fenced batch and verify every normalized detail row."""
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _literal, _rows,
    )

    identity = str(UUID(batch_id))
    if not run_id or type(max_rows_per_family) is not int \
            or not 1 <= max_rows_per_family <= 65_536:
        raise ValueError("V4 readback scope or memory bound is invalid")
    filters = (f"WHERE run_id={_literal(run_id)} "
               f"AND batch_id=toUUID({_literal(identity)}) ")
    commit_columns = ",".join(name for name, _ in
                              _CONTRACTS["trading_commit_v4"].columns)
    commits = _rows(client, f"SELECT {commit_columns} FROM arte.trading_commit_v4 "
                    f"{filters}LIMIT 2 FORMAT JSONEachRow")
    if len(commits) != 1:
        raise RuntimeError("V4 commit is missing or ambiguous")
    commit = commits[0]
    if commit["run_id"] != run_id or str(UUID(str(commit["batch_id"]))) != identity:
        raise RuntimeError("V4 commit differs from requested identity")
    family_columns = ",".join(name for name, _ in
                              _CONTRACTS["trading_commit_family_v4"].columns)
    family_rows = _rows(client,
        f"SELECT {family_columns} FROM arte.trading_commit_family_v4 "
        f"{filters}LIMIT 257 FORMAT JSONEachRow")
    if len(family_rows) > 256 or len(family_rows) != commit["family_count"]:
        raise RuntimeError("V4 commit family readback is incomplete or unbounded")
    details = _load_verified_details_v4(
        client, run_id=run_id, batch_id=identity,
        family_rows=family_rows, max_rows_per_family=max_rows_per_family)
    try:
        verify_commit_v4(commit, family_rows, details)
    except ValueError as exc:
        raise RuntimeError("V4 committed family seal differs from readback") from exc
    return commit, tuple(family_rows)


def _load_verified_details_v4(
    client, *, run_id: str, batch_id: str,
    family_rows: Sequence[Mapping], max_rows_per_family: int,
) -> dict[str, list[tuple[str, str]]]:
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _canonical_typed_content, _literal, _rows,
    )

    filters = (f"WHERE run_id={_literal(run_id)} "
               f"AND batch_id=toUUID({_literal(batch_id)}) ")
    details = {}
    related_rows = {}
    for family in family_rows:
        name = str(family["family_name"])
        contract = _CONTRACTS.get(name)
        if (contract is None or name in {"trading_commit_v4",
                                          "trading_commit_family_v4"}
                or not {"record_id", "content_hash", "run_id", "batch_id"}
                <= {column for column, _ in contract.columns}
                or type(family["row_count"]) is not int
                or not 1 <= family["row_count"] <= max_rows_per_family):
            raise RuntimeError("V4 commit names an unbounded or untyped family")
        columns = ",".join(column for column, _ in contract.columns)
        rows = _rows(client, f"SELECT {columns} FROM arte.{name} "
                     f"{filters}LIMIT {family['row_count'] + 1} FORMAT JSONEachRow")
        if len(rows) != family["row_count"]:
            raise RuntimeError("V4 detail readback has missing or excess rows")
        identities = []
        for row in rows:
            content = {key: value for key, value in row.items()
                       if key != "content_hash"}
            digest = sha256(canonical_json(_canonical_typed_content(
                name, content, stored_utc=True)).encode()).hexdigest()
            if (row["run_id"] != run_id
                    or str(UUID(str(row["batch_id"]))) != batch_id
                    or row["content_hash"] != digest):
                raise RuntimeError("V4 typed detail differs from its row hash")
            identities.append((str(UUID(str(row["record_id"]))), digest))
        details[name] = identities
        if name in {"trading_event_v1", "trading_strategy_intent_v1",
                    ENTRY_EVIDENCE.name}:
            related_rows[name] = rows
    parents = {str(UUID(str(row["record_id"]))): row for row in
               related_rows.get("trading_strategy_intent_v1", ())
               if row["reason"] == "strategy_one_entry"}
    children = related_rows.get(ENTRY_EVIDENCE.name, ())
    if len(parents) != len(children):
        raise RuntimeError("V4 Strategy 1 entry evidence is missing or extra")
    events = {str(UUID(str(row["record_id"]))): row for row in
              related_rows.get("trading_event_v1", ())}
    seen = set()
    for child in children:
        parent_id = str(UUID(str(child["parent_record_id"])))
        if parent_id in seen or parent_id not in parents or parent_id not in events:
            raise RuntimeError("V4 Strategy 1 entry evidence has no unique parent")
        seen.add(parent_id)
        try:
            _validate_strategy_one_entry_link(child, parents[parent_id],
                                              events[parent_id], run_id, batch_id)
        except ValueError as exc:
            raise RuntimeError("V4 Strategy 1 entry evidence differs from its parent") from exc
    return details


def _verify_prior_commit_v4(client, batch) -> None:
    """Reject stale/forked prefixes before writes; Keeper still owns exclusion.

    These SELECTs are not an atomic claim. The publisher separately reserves
    the exact batch in Keeper before issuing its first INSERT.
    """
    from src.trading_runtime.arte_journal_writer import _CONTRACTS, _literal, _rows

    nil = str(UUID(int=0))
    siblings = _rows(client,
        "SELECT batch_id FROM arte.trading_commit_v4 "
        f"WHERE run_id={_literal(batch.run_id)} "
        f"AND first_sequence={batch.first_sequence} "
        "LIMIT 2 FORMAT JSONEachRow")
    if siblings:
        raise RuntimeError("V4 run prefix already has a committed batch at this sequence")
    if batch.first_sequence == 1:
        if batch.prior_batch_id != nil:
            raise RuntimeError("V4 first batch must start at the nil predecessor")
        return
    if batch.prior_batch_id == nil:
        raise RuntimeError("V4 continuation lacks a prior committed batch")
    columns = ",".join(name for name, _ in
                       _CONTRACTS["trading_commit_v4"].columns)
    previous = _rows(client,
        f"SELECT {columns} FROM arte.trading_commit_v4 "
        f"WHERE run_id={_literal(batch.run_id)} "
        f"AND batch_id=toUUID({_literal(batch.prior_batch_id)}) "
        "LIMIT 2 FORMAT JSONEachRow")
    if len(previous) != 1:
        raise RuntimeError("V4 continuation lacks one committed predecessor")
    row = previous[0]
    content = {key: value for key, value in row.items()
               if key not in {"committed_at", "content_hash"}}
    if (row["run_id"] != batch.run_id
            or str(UUID(str(row["batch_id"]))) != batch.prior_batch_id
            or row["run_month"] != batch.run_month.isoformat()
            or row["status"] != "running"
            or row["last_sequence"] != batch.first_sequence - 1
            or row["event_count"] != row["last_sequence"] - row["first_sequence"] + 1
            or sha256(canonical_json(content).encode()).hexdigest()
               != row["content_hash"]):
        raise RuntimeError("V4 predecessor does not seal the contiguous run prefix")


def _compact_verified_v4_batch(dispatch, batch, families, family_rows, commit) -> None:
    """Seal exact acknowledged INSERTs and advance the Keeper watermark."""
    operations = tuple(
        (name, f"{batch.batch_id}:{name}:v4")
        for name, rows in families if rows
    ) + tuple(
        ("trading_commit_family_v4",
         f"{batch.batch_id}:family:{row['family_name']}")
        for row in family_rows
    ) + (("trading_commit_v4", f"{batch.batch_id}:commit:v4"),)
    for table, token in operations:
        dispatch.seal_verified_operation(
            run_id=batch.run_id, table=table, token=token, required=True,
            batch_id=batch.batch_id, batch_last_sequence=batch.last_sequence)
    dispatch.compact_verified_batch(
        run_id=batch.run_id, batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id,
        first_sequence=batch.first_sequence,
        last_sequence=batch.last_sequence,
        commit_hash=sha256(canonical_json(commit).encode()).hexdigest(),
        operations=operations)


def publish_base_typed_batch_v4(client, batch) -> str:
    """Publish a base typed batch with detail-first, commit-last V4 fencing.

    Backtest V3-specific child families require a separate complete extension;
    this base path cannot silently omit them. The execution thread must invoke
    this on a bounded writer lane, never inline with market-data processing.
    """
    if getattr(batch, "status", None) != "running":
        raise ValueError("V4 base publication needs one bounded running event batch")
    return _publish_typed_batch_v4(client, batch)


def publish_strategy_one_entry_batch_v4(client, batch, *, entry_evidence) -> str:
    """Commit a numbered entry and its scalar child on the writer lane."""
    return _publish_typed_batch_v4(
        client, batch, strategy_one_entry_rows=entry_evidence)


def publish_terminal_typed_batch_v4(client, batch, *, captures) -> V4CommittedPrefix:
    """Commit one lifecycle-last suffix, then anchor every account recovery.

    An interrupted anchor leaves a terminal V4 commit but no certified
    terminal recovery; retrying the same batch publishes only missing rows.
    This function belongs on the bounded writer lane, never the market loop.
    """
    from src.trading_runtime.arte_backtest_snapshot_anchor import (
        publish_terminal_backtest_snapshots,
    )
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot

    if (getattr(batch, "status", None) not in {"completed", "stopped", "failed"}
            or len(batch.events) != 1 or len(batch.run_transitions) != 1):
        raise ValueError("V4 terminal needs one lifecycle-last typed event")
    event, transition = batch.events[0], batch.run_transitions[0]
    try:
        terminal_at = datetime.fromisoformat(
            str(event["event_time"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise ValueError("V4 terminal lifecycle clock is invalid") from exc
    if (event.get("category") != "lifecycle"
            or event.get("entity_type") != "run"
            or event.get("entity_id") != batch.run_id
            or event.get("account_id") != ""
            or event.get("sequence") != batch.last_sequence
            or transition.get("record_id") != event.get("record_id")
            or transition.get("status") != batch.status
            or transition.get("account_id") != ""
            or transition.get("source_event_time") != event.get("event_time")
            or terminal_at.tzinfo is None):
        raise ValueError("V4 terminal lifecycle differs from its batch")
    if (not isinstance(captures, tuple) or not captures
            or any(type(row) is not CapturedPortfolioSnapshot for row in captures)
            or len({row.account_id for row in captures}) != len(captures)
            or any(row.run_id != batch.run_id
                   or row.state_revision != batch.last_sequence
                   or row.snapshot_at.tzinfo is None
                   or row.snapshot_at.astimezone(timezone.utc)
                   != terminal_at.astimezone(timezone.utc)
                   for row in captures)):
        raise ValueError("V4 terminal account captures are incomplete")
    context = load_typed_run_context(client, batch.run_id)
    if (context["mode"] != "backtest"
            or set(context["account_ids"]) != {row.account_id for row in captures}):
        raise ValueError("V4 terminal account membership differs from run context")
    _publish_typed_batch_v4(client, batch)
    prefix = load_verified_v4_prefix(client, batch.run_id)
    if (prefix is None or prefix.status != batch.status
            or prefix.last_batch_id != batch.batch_id
            or prefix.last_sequence != batch.last_sequence):
        raise RuntimeError("V4 terminal commit lacks exact cold readback")
    publish_terminal_backtest_snapshots(client, prefix, captures)
    return prefix


def _sealed_strategy_one_entry_rows(batch, base_families, source_rows):
    """Require one scalar child for every numbered entry intent in this batch."""
    from src.trading_runtime.arte_journal_writer import typed_row

    parents = {str(UUID(str(row["record_id"]))): row for name, rows in base_families
               if name == "trading_strategy_intent_v1" for row in rows
               if row["reason"] == "strategy_one_entry"}
    events = {str(UUID(str(row["record_id"]))): row for name, rows in base_families
              if name == "trading_event_v1" for row in rows}
    if len(source_rows) != len(parents):
        raise ValueError("V4 Strategy 1 entry intent lacks exact normalized evidence")
    seen = set()
    sealed = []
    for source in source_rows:
        row = typed_row(ENTRY_EVIDENCE.name, source)
        parent_id = str(UUID(str(row["parent_record_id"])))
        parent = parents.get(parent_id)
        event = events.get(parent_id)
        if parent_id in seen or parent is None or event is None:
            raise ValueError("V4 Strategy 1 entry evidence has no unique parent intent")
        seen.add(parent_id)
        _validate_strategy_one_entry_link(row, parent, event,
                                          batch.run_id, batch.batch_id)
        sealed.append(row)
    return tuple(sealed)


def _validate_strategy_one_entry_link(row, parent, event, run_id, batch_id):
    from datetime import time

    source = str(event["event_time"]).replace("Z", "+00:00")
    clock = datetime.fromisoformat(source)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    local = clock.astimezone(ZoneInfo("America/New_York"))
    start = datetime.combine(local.date(), time(4), tzinfo=local.tzinfo)
    elapsed = local - start
    boundary_ms = (elapsed.days * 86_400_000 + elapsed.seconds * 1_000
                   + elapsed.microseconds // 1_000)
    if (row["run_id"] != run_id
            or str(UUID(str(row["batch_id"]))) != batch_id
            or row["event_month"] != parent["event_month"]
            or event["account_id"] != parent["account_id"]
            or (event["category"], event["entity_type"])
               != ("strategy", "strategy_intent")
            or parent["intent_id"] != event["entity_id"]
            or parent["action"] != "enter_long"
            or parent["protection_profile_id"]
               != "early-squeeze-fixed-stop-full-target"
            or row["strategy_number"] != 1
            or row["boundary_ms"] != boundary_ms
            or elapsed.microseconds % 1_000
            or Decimal(str(row["frozen_gap"])) <= 0
            or not 0 < row["episode_start_ms"] <= boundary_ms
            or not 0 < row["bos_break_boundary_ms"] <= boundary_ms
            or not row["assignment_id"] or not row["target_level_id"]
            or not row["bos_support_level_id"]):
        raise ValueError("V4 Strategy 1 entry evidence differs from its typed parent")


def _publish_typed_batch_v4(client, batch, *, strategy_one_entry_rows=()) -> str:
    from src.trading_runtime.arte_journal_writer import (
        TypedJournalBatch, _CONTRACTS, _identity, _insert, _literal, _rows,
        _sealed_families, _v4_family_table, _verify_commission_links,
        _verify_exact_intent_uses, _verify_order_context_links,
    )
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(batch, TypedJournalBatch)
            or not 1 <= len(batch.events) <= 512
            or batch.status not in {"running", "completed", "stopped", "failed"}):
        raise ValueError("V4 publication needs one bounded typed event batch")
    if (getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)):
        raise RuntimeError("V4 publication requires a strict Keeper-fenced insert dispatch")
    dispatch = client.typed_insert_dispatch
    base_families = _sealed_families(batch)
    entry_rows = _sealed_strategy_one_entry_rows(
        batch, base_families, strategy_one_entry_rows)
    families = tuple((_v4_family_table(name), rows)
                     for name, rows in base_families)
    if entry_rows:
        families += ((ENTRY_EVIDENCE.name, entry_rows),)
    commit, family_rows = prepare_commit_v4(
        run_id=batch.run_id, run_month=batch.run_month,
        attempt_id=batch.attempt_id, batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id,
        first_sequence=batch.first_sequence, last_sequence=batch.last_sequence,
        source_cursor=batch.source_cursor, status=batch.status,
        sealed_families=families, committed_at=datetime.now(timezone.utc))
    filters = (f"WHERE run_id={_literal(batch.run_id)} "
               f"AND batch_id=toUUID({_literal(batch.batch_id)}) ")
    commit_columns = ",".join(name for name, _ in
                              _CONTRACTS["trading_commit_v4"].columns)
    existing_commits = _rows(client,
        f"SELECT {commit_columns} FROM arte.trading_commit_v4 "
        f"{filters}LIMIT 2 FORMAT JSONEachRow")
    if existing_commits:
        existing, _ = load_verified_commit_v4(
            client, run_id=batch.run_id, batch_id=batch.batch_id)
        if existing["content_hash"] != commit["content_hash"]:
            raise RuntimeError("V4 batch conflicts with a committed cursor")
        dispatch.assert_next_batch(
            run_id=batch.run_id, batch_id=batch.batch_id,
            prior_batch_id=batch.prior_batch_id,
            first_sequence=batch.first_sequence,
            last_sequence=batch.last_sequence)
        _compact_verified_v4_batch(
            dispatch, batch, families, family_rows, existing)
        return batch.batch_id

    _verify_prior_commit_v4(client, batch)

    # Relationships to earlier records must be checked against a committed
    # V4 prefix before any detail row is inserted. An uncommitted orphan detail
    # is never sufficient evidence for a fill, fee, or OMS command.
    _verify_commission_links(
        client, batch, base_families, journal_profile="backtest_v4")
    _verify_exact_intent_uses(
        client, batch, base_families, journal_profile="backtest_v4")
    _verify_order_context_links(
        client, batch, base_families, journal_profile="backtest_v4")
    dispatch.assert_next_batch(
        run_id=batch.run_id, batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id,
        first_sequence=batch.first_sequence,
        last_sequence=batch.last_sequence)

    for name, rows in families:
        if not rows:
            continue
        existing = _rows(client,
            f"SELECT record_id,content_hash FROM arte.{name} "
            f"{filters}FORMAT JSONEachRow")
        identities = sorted((str(UUID(str(row["record_id"]))),
                             str(row["content_hash"])) for row in existing)
        expected = _identity(rows)
        if existing and identities != expected:
            raise RuntimeError("V4 typed detail conflicts with a prior attempt")
        if not existing:
            _insert(client, name, tuple(rows), f"{batch.batch_id}:{name}:v4",
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
    actual_details = _load_verified_details_v4(
        client, run_id=batch.run_id, batch_id=batch.batch_id,
        family_rows=family_rows, max_rows_per_family=65_536)
    verify_commit_v4(commit, family_rows, actual_details)

    family_columns = ",".join(name for name, _ in
                              _CONTRACTS["trading_commit_family_v4"].columns)
    existing_families = _rows(client,
        f"SELECT {family_columns} FROM arte.trading_commit_family_v4 "
        f"{filters}LIMIT 257 FORMAT JSONEachRow")
    by_name = {}
    for row in existing_families:
        name = str(row["family_name"])
        if name in by_name:
            raise RuntimeError("V4 family publication repeated a family")
        by_name[name] = row
    if set(by_name) - {row["family_name"] for row in family_rows}:
        raise RuntimeError("V4 family publication includes foreign evidence")
    for row in family_rows:
        prior = by_name.get(row["family_name"])
        if prior is not None and prior != row:
            raise RuntimeError("V4 family publication conflicts with a prior attempt")
        if prior is None:
            _insert(client, "trading_commit_family_v4", (row,),
                    f"{batch.batch_id}:family:{row['family_name']}",
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
    verified_families = _rows(client,
        f"SELECT {family_columns} FROM arte.trading_commit_family_v4 "
        f"{filters}LIMIT 257 FORMAT JSONEachRow")
    if sorted(verified_families, key=lambda row: row["family_name"]) != list(family_rows):
        raise RuntimeError("V4 family publication lacks complete readback")
    _insert(client, "trading_commit_v4", (commit,),
            f"{batch.batch_id}:commit:v4", dispatch_batch_id=batch.batch_id,
            dispatch_sequence=batch.last_sequence)
    loaded, _ = load_verified_commit_v4(
        client, run_id=batch.run_id, batch_id=batch.batch_id)
    if loaded["content_hash"] != commit["content_hash"]:
        raise RuntimeError("V4 committed cursor differs from the intended batch")
    _compact_verified_v4_batch(
        dispatch, batch, families, family_rows, loaded)
    return batch.batch_id
