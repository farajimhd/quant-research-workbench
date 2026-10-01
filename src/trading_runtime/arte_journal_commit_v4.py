"""Narrow, normalized Strategy 1 commit authority (pre-publication contract).

The writer must verify every typed detail family before publishing these rows.
No runtime may treat either row alone as a durable fence: recovery requires the
unique commit, its complete child-family set, and the detail-row readback.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from time import perf_counter_ns
from typing import Mapping, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_strategy_one_entry_schema import (
    ADD_EVIDENCE, ENTRY_EVIDENCE,
)
from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT
from src.trading_runtime.arte_broker_acknowledgement_v5 import ACKNOWLEDGEMENT_V5
from src.trading_runtime.arte_order_cancel_v4 import CANCEL
from src.trading_runtime.arte_order_reprice_v4 import REPRICE
from src.trading_runtime.arte_order_modify_command_v1 import MODIFY_COMMAND
from src.trading_runtime.arte_portfolio_allocation_v4 import (
    ALLOCATION as V4_ALLOCATION, seal_portfolio_allocation_v3,
)
from src.trading_runtime.arte_reservation_reason_v4 import (
    RESERVATION_REASON, seal_reservation_reason_family_v3,
)
from src.trading_runtime.arte_risk_action_v4 import (
    ACTION as RISK_ACTION, REPLY as RISK_REPLY, seal_risk_action_v4,
)
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    RECONCILIATION as PROTECTION_RECONCILIATION,
    ACTION as RECONCILIATION_ACTION, REPLY as RECONCILIATION_REPLY,
    seal_protection_reconciliation_v4,
)
from src.backend.backtest_protection_change_v3 import (
    CHANGE as PROTECTION_CHANGE, ENTRY_ORDER as PROTECTION_ENTRY_ORDER,
    seal_protection_changes_v3,
)


from .arte_followthrough_failure_v4 import FAILURE, seal_followthrough_rows
from .arte_rising_momentum_entry_v4 import (
    MOMENTUM, seal_rising_momentum_rows, momentum_select_columns, decode_momentum_row,
)
from .arte_initial_momentum_entry_v4 import (
    INITIAL_MOMENTUM, seal_initial_momentum_rows, initial_momentum_select_columns,
    decode_initial_momentum_row,
)
from .arte_first_price_entry_v4 import FIRST_PRICE, seal_first_price_rows

_MULTIROW_FAMILIES = frozenset({PROTECTION_ENTRY_ORDER.name,
                                RESERVATION_REASON.name})
MAX_V4_COMMIT_EVENTS = 4096


def _same_utc_time(left, right) -> bool:
    """Compare DateTime64(9) event and DateTime64(6) detail clocks."""
    def parse(value):
        raw = str(value)
        fraction = re.search(r"\.(\d+)(?:Z|[+-]\d\d:\d\d)?$", raw)
        digits = fraction.group(1) if fraction else ""
        if len(digits) > 9:
            raise ValueError("Journal clock exceeds DateTime64(9) precision")
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        utc = (at if at.tzinfo is not None
               else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        return utc, int(digits[6:].ljust(3, "0") or "0")

    return parse(left) == parse(right)


@dataclass(frozen=True, slots=True)
class V4CommittedPrefix:
    """Verified normalized run chain, not an admission or write lease."""

    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


def load_writer_v4_snapshot_prefix(client, run_id: str, *,
                                   max_commits: int = 100_000,
                                   first_price_source=None,
                                   ) -> V4CommittedPrefix | None:
    """Verify a writer-owned head without rehashing its older compacted batches.

    This shortcut is limited to the current exclusive Backtest writer session.
    Every batch was detail-verified before Keeper compaction. The first
    snapshot at a head re-verifies its current details; later snapshots on
    this same dedicated writer client may reuse that proof after rechecking
    the Keeper head and immutable commit row. Cold readers scan the full prefix.
    """
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.arte_journal_writer import _CONTRACTS, _literal, _rows

    price_scope = None
    if first_price_source is not None:
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_source.run_id != run_id:
            raise RuntimeError("V4 writer snapshot has a foreign certified price source")
        price_scope = (first_price_source.run_id, first_price_source.plan.token,
                       first_price_source.plan.source.token)

    lease = getattr(client, "backtest_v4_lease", None)
    if lease is None:
        # Older injected test clients and non-Backtest consumers retain the
        # complete cold verifier. A present but invalid lease never falls back.
        if first_price_source is not None:
            return load_verified_v4_prefix(client, run_id, max_commits=max_commits,
                                           first_price_source=first_price_source)
        return (load_verified_v4_prefix(client, run_id)
                if max_commits == 100_000 else load_verified_v4_prefix(
                    client, run_id, max_commits=max_commits))
    dispatch = getattr(client, "typed_insert_dispatch", None)
    if (not isinstance(lease, BacktestV4KeeperLease)
            or lease.run_id != run_id or lease.epoch < 1
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not lease.owner._session.client
            or type(max_commits) is not int or not 1 <= max_commits <= 100_000):
        raise RuntimeError("V4 warm snapshot lacks its original exclusive writer")
    lease.assert_current()
    gate, _ = dispatch._read_gate(run_id)
    zero = str(UUID(int=0))
    if (gate.mode != "open" or gate.inflight or gate.registered
            or gate.active_batch_id != zero or gate.compacted_through < 1):
        raise RuntimeError("V4 warm snapshot has an unsealed dispatch gate")
    columns = ",".join(name for name, _ in _CONTRACTS["trading_commit_v4"].columns)
    cached = getattr(client, "_v4_writer_snapshot_cache", None)
    if cached is not None and getattr(client, "_v4_writer_snapshot_price_scope", None) != price_scope:
        raise RuntimeError("V4 writer snapshot cached price authority differs")
    if cached is None and lease.epoch > 1:
        # A replacement writer cannot inherit the first process's compacted
        # proof. Verify the entire old chain once, then cache that head under
        # this lease before permitting the ordinary warm-head shortcut.
        prefix = (load_verified_v4_prefix(client, run_id, max_commits=max_commits)
                  if first_price_source is None else load_verified_v4_prefix(client, run_id,
                      max_commits=max_commits, first_price_source=first_price_source))
        if (prefix is None or prefix.last_sequence != gate.compacted_through
                or prefix.last_batch_id != gate.compacted_batch_id
                or prefix.status != "running"):
            raise RuntimeError("V4 resumed snapshot differs from Keeper compaction")
        rows = _rows(client,
            f"SELECT {columns} FROM arte.trading_commit_v4 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
            "LIMIT 2 FORMAT JSONEachRow")
        if (len(rows) != 1
                or sha256(canonical_json(rows[0]).encode()).hexdigest()
                != gate.compacted_commit_hash):
            raise RuntimeError("V4 resumed snapshot head differs from Keeper")
        lease.assert_current()
        client._v4_writer_snapshot_cache = (prefix, gate.compacted_commit_hash)
        client._v4_writer_snapshot_price_scope = price_scope
        return prefix
    if cached is not None:
        prefix, digest = cached
        if (not isinstance(prefix, V4CommittedPrefix)
                or prefix.run_id != run_id or type(digest) is not str):
            raise RuntimeError("V4 warm snapshot cache has a foreign authority")
        if len(prefix.batch_ids) > max_commits:
            raise RuntimeError("V4 warm snapshot exceeds bounded committed chain")
        if (prefix.last_sequence == gate.compacted_through
                and prefix.last_batch_id == gate.compacted_batch_id
                and digest == gate.compacted_commit_hash):
            rows = _rows(client,
                f"SELECT {columns} FROM arte.trading_commit_v4 "
                f"WHERE run_id={_literal(run_id)} "
                f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
                "LIMIT 2 FORMAT JSONEachRow")
            if (len(rows) != 1
                    or sha256(canonical_json(rows[0]).encode()).hexdigest() != digest
                    or rows[0]["last_sequence"] != prefix.last_sequence
                    or rows[0]["source_cursor"] != prefix.source_cursor):
                raise RuntimeError("V4 warm snapshot cache differs from commit")
            lease.assert_current()
            return prefix
    commits = _rows(client,
        f"SELECT {columns} FROM arte.trading_commit_v4 "
        f"WHERE run_id={_literal(run_id)} ORDER BY first_sequence,batch_id "
        f"LIMIT {max_commits + 1} FORMAT JSONEachRow")
    if not commits or len(commits) > max_commits:
        raise RuntimeError("V4 warm snapshot has no bounded committed chain")
    prior, sequence, ids, seen = zero, 0, [], set()
    month = commits[0]["run_month"]
    for row in commits:
        identity = str(UUID(str(row["batch_id"])))
        cursor = row["source_cursor"]
        if (row["run_id"] != run_id or row["run_month"] != month
                or str(UUID(str(row["prior_batch_id"]))) != prior
                or row["first_sequence"] != sequence + 1
                or row["last_sequence"] < row["first_sequence"]
                or row["event_count"] != row["last_sequence"] - sequence
                or row["status"] != "running" or identity in seen
                or not isinstance(cursor, str) or not cursor
                or cursor.lstrip("\ufeff \t\r\n").startswith(("{", "["))):
            raise RuntimeError("V4 warm snapshot chain is forked or incomplete")
        prior, sequence = identity, row["last_sequence"]
        ids.append(identity)
        seen.add(identity)
    latest = commits[-1]
    if (sequence != gate.compacted_through
            or prior != gate.compacted_batch_id
            or sha256(canonical_json(latest).encode()).hexdigest()
               != gate.compacted_commit_hash):
        raise RuntimeError("V4 warm snapshot differs from Keeper compaction")
    verified, _ = (load_verified_commit_v4(client, run_id=run_id, batch_id=prior)
                  if first_price_source is None else load_verified_commit_v4(client,
                      run_id=run_id, batch_id=prior, first_price_source=first_price_source))
    if verified != latest:
        raise RuntimeError("V4 warm snapshot current detail differs from commit")
    lease.assert_current()
    prefix = V4CommittedPrefix(run_id, sequence, prior, latest["source_cursor"],
                               "running", tuple(ids))
    client._v4_writer_snapshot_cache = (prefix, gate.compacted_commit_hash)
    client._v4_writer_snapshot_price_scope = price_scope
    return prefix


def load_verified_v4_prefix(client, run_id: str, *,
                            max_commits: int = 100_000,
                            first_price_source=None) -> V4CommittedPrefix | None:
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
        preceding = (V4CommittedPrefix(
            run_id, last_sequence, prior_id, commits[len(batch_ids)-1]['source_cursor'],
            status, tuple(batch_ids)) if batch_ids else None)
        verified, _ = load_verified_commit_v4(
            client, run_id=run_id, batch_id=batch_id, first_price_source=first_price_source,
            **({'verified_prior_prefix': preceding} if preceding is not None else {}))
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
        key = (identities if name in _MULTIROW_FAMILIES
               else [record_id for record_id, _ in identities])
        if len(set(key)) != len(identities):
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
            key = (normalized if name in _MULTIROW_FAMILIES
                   else [record_id for record_id, _ in normalized])
            if (len(normalized) != row["row_count"]
                    or len(set(key)) != len(normalized)
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
    first_price_source=None,
    first_price_authorities: tuple = (),
    verified_prior_prefix: V4CommittedPrefix | None = None,
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
    if verified_prior_prefix is not None and (
            type(verified_prior_prefix) is not V4CommittedPrefix
            or verified_prior_prefix.run_id != run_id
            or verified_prior_prefix.status != 'running'
            or not verified_prior_prefix.batch_ids
            or verified_prior_prefix.last_batch_id != verified_prior_prefix.batch_ids[-1]
            or verified_prior_prefix.last_batch_id != str(commit['prior_batch_id'])
            or verified_prior_prefix.last_sequence + 1 != commit['first_sequence']):
        raise RuntimeError('V4 source prefix does not immediately precede requested commit')
    family_columns = ",".join(name for name, _ in
                              _CONTRACTS["trading_commit_family_v4"].columns)
    family_rows = _rows(client,
        f"SELECT {family_columns} FROM arte.trading_commit_family_v4 "
        f"{filters}LIMIT 257 FORMAT JSONEachRow")
    if len(family_rows) > 256 or len(family_rows) != commit["family_count"]:
        raise RuntimeError("V4 commit family readback is incomplete or unbounded")
    details = _load_verified_details_v4(
        client, run_id=run_id, batch_id=identity,
        family_rows=family_rows, max_rows_per_family=max_rows_per_family,
        batched_readback=bool(getattr(client, "v4_batched_detail_readback", False)),
        prior_batch_id=str(commit["prior_batch_id"]), first_price_source=first_price_source,
        first_price_authorities=first_price_authorities,
        verified_prior_prefix=verified_prior_prefix)
    try:
        verify_commit_v4(commit, family_rows, details)
    except ValueError as exc:
        raise RuntimeError("V4 committed family seal differs from readback") from exc
    return commit, tuple(family_rows)


def _load_verified_details_v4(
    client, *, run_id: str, batch_id: str,
    family_rows: Sequence[Mapping], max_rows_per_family: int,
    batched_readback: bool = False, prior_batch_id: str | None = None,
    first_price_authorities: tuple = (),
    first_price_source=None,
    verified_prior_prefix: V4CommittedPrefix | None = None,
) -> dict[str, list[tuple[str, str]]]:
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _canonical_typed_content, _literal, _rows,
    )

    filters = (f"WHERE run_id={_literal(run_id)} "
               f"AND batch_id=toUUID({_literal(batch_id)}) ")
    details = {}
    related_rows = {}
    family_specs = []
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
        if name in {spec[0] for spec in family_specs}:
            raise RuntimeError("V4 commit repeats a typed family")
        family_specs.append((name, tuple(column for column, _ in contract.columns),
                             family["row_count"]))
    row_sets = (
        _batched_detail_rows_v4(client, tuple(spec for spec in family_specs
            if spec[0] not in (MOMENTUM.name, INITIAL_MOMENTUM.name)), filters)
        if batched_readback else None
    )
    for name, column_names, row_count in family_specs:
        if name in (MOMENTUM.name, INITIAL_MOMENTUM.name):
            columns = momentum_select_columns if name == MOMENTUM.name else initial_momentum_select_columns
            decode = decode_momentum_row if name == MOMENTUM.name else decode_initial_momentum_row
            rows = [decode(row) for row in _rows(client,
                f"SELECT {columns()} FROM arte.{name} "
                f"{filters}LIMIT {row_count + 1} FORMAT JSONEachRow")]
        else:
            rows = (row_sets[name] if row_sets is not None else _rows(
                client, f"SELECT {','.join(column_names)} FROM arte.{name} "
                f"{filters}LIMIT {row_count + 1} FORMAT JSONEachRow"))
        if len(rows) != row_count:
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
                    ENTRY_EVIDENCE.name, ADD_EVIDENCE.name, FAILURE.name, MOMENTUM.name, INITIAL_MOMENTUM.name, FIRST_PRICE.name,
                    ACKNOWLEDGEMENT.name,
                    ACKNOWLEDGEMENT_V5.name, CANCEL.name,
                    REPRICE.name, MODIFY_COMMAND.name,
                    V4_ALLOCATION.name, RESERVATION_REASON.name,
                    "trading_portfolio_reservation_event_v1",
                    RISK_ACTION.name, RISK_REPLY.name,
                    PROTECTION_CHANGE.name, PROTECTION_ENTRY_ORDER.name,
                    PROTECTION_RECONCILIATION.name,
                    RECONCILIATION_ACTION.name, RECONCILIATION_REPLY.name,
                    "trading_backtest_account_snapshot_v2",
                    "trading_backtest_position_snapshot_v2",
                    "trading_oms_group_state_v1",
                    "trading_oms_execution_tactic_v1",
                    "trading_oms_execution_step_v1"}:
            related_rows[name] = rows
    if ("trading_oms_execution_tactic_v1" in related_rows
            or "trading_oms_execution_step_v1" in related_rows):
        from .arte_oms_tactic_projection import seal_oms_tactic_rows

        try:
            seal_oms_tactic_rows(
                tuple(related_rows.get("trading_oms_execution_tactic_v1", ())),
                tuple(related_rows.get("trading_oms_execution_step_v1", ())),
                tuple(related_rows.get("trading_oms_group_state_v1", ())),
                tuple(related_rows.get("trading_event_v1", ())),
                run_id=run_id, batch_id=batch_id, stored_utc=True)
        except ValueError as exc:
            raise RuntimeError("V4 OMS tactic differs from its group revision") from exc
    seal_followthrough_rows(client, related_rows.get(FAILURE.name, ()),
        related_rows.get("trading_strategy_intent_v1", ()),
        related_rows.get("trading_event_v1", ()), related_rows.get(ENTRY_EVIDENCE.name, ()),
        prior_batch_id=prior_batch_id)
    from .arte_profit_giveback_v4 import PROFIT_GIVEBACK, seal_profit_giveback_rows
    from .strategy_profit_giveback_exit import REASON as PROFIT_REASON
    profit_rows = related_rows.get(PROFIT_GIVEBACK.name, ())
    if profit_rows or any(row['reason'] == PROFIT_REASON for row in
                          related_rows.get('trading_strategy_intent_v1', ())):
        if verified_prior_prefix is None:
            raise RuntimeError('Profit readback requires an independently verified preceding prefix')
        seal_profit_giveback_rows(client, profit_rows,
            related_rows.get('trading_strategy_intent_v1', ()),
            related_rows.get('trading_event_v1', ()),
            prefix=verified_prior_prefix, first_price_source=first_price_source)
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
    try:
        seal_rising_momentum_rows(related_rows.get(MOMENTUM.name, ()), children,
                                 related_rows.get("trading_strategy_intent_v1", ()),
                                 related_rows.get("trading_event_v1", ()))
        seal_initial_momentum_rows(related_rows.get(INITIAL_MOMENTUM.name, ()), children,
                                 related_rows.get("trading_strategy_intent_v1", ()),
                                 related_rows.get("trading_event_v1", ()),
                                 related_rows.get(MOMENTUM.name, ()))
        if first_price_source is not None:
            from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
            if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_authorities:
                raise ValueError("Cold first-price readback requires one certified source authority")
            first_price_authorities = first_price_source.resolve(run_id, children,
                related_rows.get("trading_strategy_intent_v1", ()))
        # ClickHouse JSON renders this schema's DateTime64(..., 'UTC') without
        # an offset. These rows have already passed canonical stored-UTC hash
        # verification; restore the declared timezone only at this read boundary.
        price_events = related_rows.get("trading_event_v1", ())
        if any(child['strategy_number'] in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) for child in children):
            normalized_events = []
            for event in price_events:
                clock = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
                if clock.tzinfo is None:
                    clock = clock.replace(tzinfo=timezone.utc)
                normalized_events.append(dict(event, event_time=clock.isoformat()))
            price_events = tuple(normalized_events)
        seal_first_price_rows(related_rows.get(FIRST_PRICE.name, ()), children,
            related_rows.get("trading_strategy_intent_v1", ()),
            tuple(price_events), first_price_authorities)
    except ValueError as exc:
        raise RuntimeError("V4 Strategy 13 momentum evidence differs from its parent") from exc
    add_parents = {str(UUID(str(row["record_id"]))): row for row in
                   related_rows.get("trading_strategy_intent_v1", ())
                   if row["reason"] == "strategy_one_add"}
    add_children = related_rows.get(ADD_EVIDENCE.name, ())
    if len(add_parents) != len(add_children):
        raise RuntimeError("V4 Strategy 1 add evidence is missing or extra")
    seen_add = set()
    for child in add_children:
        parent_id = str(UUID(str(child["parent_record_id"])))
        if (parent_id in seen_add or parent_id not in add_parents
                or parent_id not in events):
            raise RuntimeError("V4 Strategy 1 add evidence has no unique parent")
        seen_add.add(parent_id)
        try:
            _validate_strategy_one_add_link(
                child, add_parents[parent_id], events[parent_id],
                run_id, batch_id)
        except ValueError as exc:
            raise RuntimeError("V4 Strategy 1 add evidence differs from its parent") from exc
    acknowledgements = (*related_rows.get(ACKNOWLEDGEMENT.name, ()),
                        *related_rows.get(ACKNOWLEDGEMENT_V5.name, ()))
    seen_ack = set()
    for row in acknowledgements:
        record_id = str(UUID(str(row["record_id"])))
        parent = events.get(record_id)
        if (record_id in seen_ack or parent is None
                or (parent["category"], parent["entity_type"])
                   != ("broker", "order_acknowledgement")
                or parent["entity_id"] != row["broker_order_id"]
                or parent["run_id"] != row["run_id"]
                or parent["event_month"] != row["event_month"]
                or str(UUID(str(parent["batch_id"])))
                   != str(UUID(str(row["batch_id"])))):
            raise RuntimeError("V4 broker acknowledgement differs from its event")
        seen_ack.add(record_id)
    if {record_id for record_id, row in events.items()
        if (row["category"], row["entity_type"])
           == ("broker", "order_acknowledgement")} != seen_ack:
        raise RuntimeError("V4 broker acknowledgement has missing typed detail")
    cancellations = related_rows.get(CANCEL.name, ())
    seen_cancel = set()
    for row in cancellations:
        record_id = str(UUID(str(row["record_id"])))
        parent = events.get(record_id)
        if (record_id in seen_cancel or parent is None
                or (parent["category"], parent["entity_type"])
                   not in {("command", "order_cancel"),
                           ("broker", "order_cancel_requested")}
                or parent["entity_id"] != row["broker_order_id"]
                or parent["run_id"] != row["run_id"]
                or parent["event_month"] != row["event_month"]
                or str(UUID(str(parent["batch_id"])))
                   != str(UUID(str(row["batch_id"])))):
            raise RuntimeError("V4 cancellation differs from its event")
        seen_cancel.add(record_id)
    if {record_id for record_id, row in events.items()
        if (row["category"], row["entity_type"]) in
           {("command", "order_cancel"), ("broker", "order_cancel_requested")}} != seen_cancel:
        raise RuntimeError("V4 cancellation has missing typed detail")
    repricings = related_rows.get(REPRICE.name, ())
    seen_reprice = set()
    for row in repricings:
        record_id = str(UUID(str(row["record_id"])))
        parent = events.get(record_id)
        if (record_id in seen_reprice or parent is None
                or (parent["category"], parent["entity_type"])
                   not in {("broker", "order_repriced"),
                           ("broker", "order_reprice_error")}
                or (row["result_kind"] == "modified")
                   != (parent["entity_type"] == "order_repriced")
                or parent["entity_id"] != row["broker_order_id"]
                or parent["run_id"] != row["run_id"]
                or parent["event_month"] != row["event_month"]
                or str(UUID(str(parent["batch_id"])))
                   != str(UUID(str(row["batch_id"])))):
            raise RuntimeError("V4 repricing differs from its event")
        seen_reprice.add(record_id)
    if {record_id for record_id, row in events.items()
        if (row["category"], row["entity_type"]) in
           {("broker", "order_repriced"), ("broker", "order_reprice_error")}} != seen_reprice:
        raise RuntimeError("V4 repricing has missing typed detail")
    modifications = related_rows.get(MODIFY_COMMAND.name, ())
    seen_modify = set()
    for row in modifications:
        record_id = str(UUID(str(row["record_id"])))
        parent = events.get(record_id)
        if (record_id in seen_modify or parent is None
                or (parent["category"], parent["entity_type"])
                   != ("command", "order_modify")
                or parent["entity_id"] != row["broker_order_id"]
                or parent["account_id"] != row["account_id"]
                or parent["run_id"] != row["run_id"]
                or parent["event_month"] != row["event_month"]
                or str(UUID(str(parent["batch_id"])))
                   != str(UUID(str(row["batch_id"])))
                or not _same_utc_time(parent["event_time"], row["requested_at"])
                or parent["causation_id"] != row["intent_id"]):
            raise RuntimeError("V4 modify command differs from its event")
        seen_modify.add(record_id)
    if {record_id for record_id, row in events.items()
        if (row["category"], row["entity_type"])
           == ("command", "order_modify")} != seen_modify:
        raise RuntimeError("V4 modify command has missing typed detail")
    try:
        seal_portfolio_allocation_v3(
            related_rows.get(V4_ALLOCATION.name, ()), tuple(events.values()),
            run_id=run_id, batch_id=batch_id)
    except ValueError as exc:
        raise RuntimeError("V4 portfolio allocation differs from its parent") from exc
    try:
        seal_reservation_reason_family_v3(
            related_rows.get(RESERVATION_REASON.name, ()),
            tuple(events.values()),
            related_rows.get("trading_portfolio_reservation_event_v1", ()),
            run_id=run_id, batch_id=batch_id)
    except ValueError as exc:
        raise RuntimeError("V4 reservation reasons differ from their parent") from exc
    try:
        seal_risk_action_v4(
            related_rows.get(RISK_ACTION.name, ()),
            related_rows.get(RISK_REPLY.name, ()),
            tuple(events.values()), run_id=run_id, batch_id=batch_id)
    except ValueError as exc:
        raise RuntimeError("V4 risk action differs from its reply graph") from exc
    try:
        seal_protection_changes_v3(
            related_rows.get(PROTECTION_CHANGE.name, ()),
            related_rows.get(PROTECTION_ENTRY_ORDER.name, ()),
            tuple(events.values()), run_id=run_id, batch_id=batch_id)
    except ValueError as exc:
        raise RuntimeError("V4 protection change differs from its typed children") from exc
    try:
        seal_protection_reconciliation_v4(
            related_rows.get(PROTECTION_RECONCILIATION.name, ()),
            related_rows.get(RECONCILIATION_ACTION.name, ()),
            related_rows.get(RECONCILIATION_REPLY.name, ()),
            tuple(events.values()), run_id=run_id, batch_id=batch_id)
    except ValueError as exc:
        raise RuntimeError("V4 reconciliation differs from its typed children") from exc
    accounts = related_rows.get("trading_backtest_account_snapshot_v2", ())
    positions = related_rows.get("trading_backtest_position_snapshot_v2", ())
    if accounts or positions:
        from src.backend.backtest_terminal_snapshot_v2 import recover_snapshot_group

        account_ids = set()
        covered = set()
        for row in accounts:
            record_id = str(UUID(str(row["record_id"])))
            event = events.get(record_id)
            if (event is None or record_id in covered
                    or (event["category"], event["entity_type"])
                    != ("snapshot", "portfolio")
                    or event["entity_id"] != row["account_id"]
                    or event["account_id"] != row["account_id"]
                    or row["account_id"] in account_ids):
                raise RuntimeError("V4 broker account readback lacks its event")
            account_ids.add(row["account_id"])
            covered.add(record_id)
            children = tuple(child for child in positions
                             if child["parent_snapshot_id"] == row["snapshot_id"])
            try:
                recover_snapshot_group(row, children)
            except ValueError as exc:
                raise RuntimeError("V4 broker account readback has incomplete positions") from exc
        for row in positions:
            record_id = str(UUID(str(row["record_id"])))
            event = events.get(record_id)
            if (event is None or record_id in covered
                    or (event["category"], event["entity_type"])
                    != ("snapshot", "position")
                    or event["entity_id"] != str(row["conid"])
                    or event["account_id"] != row["account_id"]
                    or row["account_id"] not in account_ids):
                raise RuntimeError("V4 broker position readback lacks its event")
            covered.add(record_id)
        snapshot_events = {record_id for record_id, row in events.items()
                           if row["category"] == "snapshot"
                           and row["entity_type"] in {"portfolio", "position"}}
        if covered != snapshot_events:
            raise RuntimeError("V4 broker snapshot readback lacks complete coverage")
    return details


def _batched_detail_rows_v4(client, family_specs, filters):
    """Read complete typed rows in bounded UNIONs; JSON is transport only.

    The stored rows remain normalized. Each row is reconstructed using its
    authoritative table contract, then checked by the unchanged canonical
    hash and cross-family validators in _load_verified_details_v4.
    """
    from src.trading_runtime.arte_journal_writer import _literal, _rows

    row_sets = {name: [] for name, _, _ in family_specs}
    for start in range(0, len(family_specs), 8):
        group = family_specs[start:start + 8]
        allowed = {name: columns for name, columns, _ in group}
        selects = [
            f"(SELECT {_literal(name)} AS family_name, "
            f"toJSONString(tuple({','.join(columns)})) AS payload "
            f"FROM arte.{name} {filters}LIMIT {row_count + 1})"
            for name, columns, row_count in group
        ]
        for envelope in _rows(client, " UNION ALL ".join(selects)
                              + " FORMAT JSONEachRow"):
            name = envelope.get("family_name")
            if name not in allowed or type(envelope.get("payload")) is not str:
                raise RuntimeError("V4 batched detail readback has a foreign family")
            try:
                values = json.loads(envelope["payload"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("V4 batched detail readback is malformed") from exc
            columns = allowed[name]
            if type(values) is not list or len(values) != len(columns):
                raise RuntimeError("V4 batched detail readback has invalid columns")
            row_sets[name].append(dict(zip(columns, values, strict=True)))
    return row_sets


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


def _family_operation_mode(dispatch, batch, family_rows) -> str:
    """Preserve retry compatibility with older per-row family INSERTs."""
    table = "trading_commit_family_v4"
    grouped = dispatch.operation_present(
        run_id=batch.run_id, table=table,
        token=f"{batch.batch_id}:family-set:v4")
    old = any(dispatch.operation_present(
        run_id=batch.run_id, table=table,
        token=f"{batch.batch_id}:family:{row['family_name']}")
        for row in family_rows)
    if grouped and old:
        raise RuntimeError("V4 family transport has mixed Keeper operations")
    return "grouped" if grouped or not old else "per_row"


def _compact_verified_v4_batch(dispatch, batch, families, family_rows, commit) -> None:
    """Seal exact acknowledged INSERTs and advance the Keeper watermark."""
    mode = _family_operation_mode(dispatch, batch, family_rows)
    operations = [
        (name, f"{batch.batch_id}:{name}:v4")
        for name, rows in families if rows
    ]
    if mode == "grouped":
        operations.append(("trading_commit_family_v4",
                           f"{batch.batch_id}:family-set:v4"))
    else:
        operations.extend(("trading_commit_family_v4",
                           f"{batch.batch_id}:family:{row['family_name']}")
                          for row in family_rows)
    operations.append(("trading_commit_v4", f"{batch.batch_id}:commit:v4"))
    operations = tuple(operations)
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


def publish_portfolio_allocation_batch_v4(client, batch, *, allocation) -> str:
    """Fence one normalized allocation fill with its parent event."""
    return _publish_typed_batch_v4(
        client, batch, portfolio_allocation_row=allocation)


def publish_reservation_reason_batch_v4(client, batch, *, reasons) -> str:
    """Fence a reservation transition and its ordered scalar reasons."""
    return _publish_typed_batch_v4(
        client, batch, reservation_reason_rows=reasons)


def publish_strategy_one_entry_batch_v4(
    client, batch, *, entry_evidence=(), add_evidence=(), momentum_evidence=(), initial_momentum_evidence=(),
    first_price_evidence=(), first_price_authorities=(),
) -> str:
    """Commit one numbered acquisition and its scalar child on the writer lane."""
    if bool(entry_evidence) == bool(add_evidence):
        raise ValueError("Strategy 1 acquisition needs exactly one evidence family")
    return _publish_typed_batch_v4(
        client, batch, strategy_one_entry_rows=entry_evidence,
        strategy_one_add_rows=add_evidence, rising_momentum_rows=momentum_evidence,
        initial_momentum_rows=initial_momentum_evidence,
        first_price_rows=first_price_evidence, first_price_authorities=first_price_authorities)


def publish_oms_tactic_batch_v4(client, batch, *, tactic_state, tactic_steps=()) -> str:
    """Commit one OMS group revision with its exact normalized tactic graph."""
    return _publish_typed_batch_v4(
        client, batch, oms_tactic_rows=(tactic_state, tuple(tactic_steps)))


def publish_broker_acknowledgement_batch_v4(client, batch, *, acknowledgement) -> str:
    """Commit the exact broker reply and its event in one V4 family fence."""
    return _publish_typed_batch_v4(
        client, batch, broker_acknowledgement_row=acknowledgement)


def publish_broker_acknowledgement_batch_v5(client, batch, *, acknowledgement) -> str:
    """Commit one live scalar reply under the same V4 family fence."""
    if getattr(client, "live_v4_lease", None) is None:
        raise RuntimeError("V5 broker acknowledgement requires the live Keeper lease")
    return _publish_typed_batch_v4(
        client, batch, broker_acknowledgement_v5_row=acknowledgement)


def publish_order_cancel_batch_v4(client, batch, *, cancellation) -> str:
    """Commit one cancellation command/result and its exact scalar detail."""
    return _publish_typed_batch_v4(
        client, batch, order_cancel_row=cancellation)


def publish_order_reprice_batch_v4(client, batch, *, repricing) -> str:
    """Commit one adaptive-order outcome and its exact scalar detail."""
    return _publish_typed_batch_v4(
        client, batch, order_reprice_row=repricing)


def publish_order_modify_command_batch_v4(client, batch, *, modification) -> str:
    """Commit a live broker modification before its external side effect."""
    if getattr(client, "live_v4_lease", None) is None:
        raise RuntimeError("Modify command requires the live Keeper lease")
    return _publish_typed_batch_v4(
        client, batch, order_modify_command_row=modification)


def publish_risk_action_batch_v4(client, batch, *, action, replies) -> str:
    """Commit one risk action and every ordered scalar broker reply."""
    return _publish_typed_batch_v4(
        client, batch, risk_action_row=action, risk_reply_rows=replies)


def publish_protection_change_batch_v4(client, batch, *, change,
                                       entry_orders) -> str:
    """Fence one protection revision and all of its normalized entry links."""
    return _publish_typed_batch_v4(
        client, batch, protection_change_row=change,
        protection_entry_order_rows=entry_orders)


def publish_protection_reconciliation_batch_v4(
    client, batch, *, reconciliation, actions, replies,
) -> str:
    return _publish_typed_batch_v4(
        client, batch, protection_reconciliation_row=reconciliation,
        protection_reconciliation_actions=actions,
        protection_reconciliation_replies=replies)


def publish_terminal_typed_batch_v4(
    client, batch, *, captures, broker_snapshots=None, first_price_source=None,
) -> V4CommittedPrefix:
    """Commit one lifecycle-last suffix, then anchor every account recovery.

    An interrupted anchor leaves a terminal V4 commit but no certified
    terminal recovery; retrying the same batch publishes only missing rows.
    This function belongs on the bounded writer lane, never the market loop.
    """
    from src.trading_runtime.arte_backtest_snapshot_anchor import (
        _publish_terminal_snapshots_after_verified_prefix,
    )
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
    from src.backend.backtest_terminal_broker_snapshot_v4 import V4BrokerSnapshotRows

    if (broker_snapshots is not None
            and type(broker_snapshots) is not V4BrokerSnapshotRows):
        raise ValueError("V4 terminal broker snapshots are not typed")
    if (getattr(batch, "status", None) not in {"completed", "stopped", "failed"}
            or len(batch.events) != (1 if broker_snapshots is None
                                     else broker_snapshots.last_sequence
                                          - broker_snapshots.first_sequence + 1)
            or len(batch.run_transitions) != 1):
        raise ValueError("V4 terminal needs a lifecycle-last typed suffix")
    event, transition = batch.events[-1], batch.run_transitions[0]
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
    if first_price_source is not None:
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        if (type(first_price_source) is not CertifiedPriceReadbackAuthority
                or first_price_source.run_id != batch.run_id
                or first_price_source.plan.source.market.sessions !=
                   (str(context["session_date"]),)):
            raise ValueError("Strategy20 terminal lacks its native session source")
    # Empty certified sessions have no native entry plan. The cold prefix
    # below still rejects any Strategy20 entry when native authority is absent.
    if (context["mode"] != "backtest"
            or set(context["account_ids"]) != {row.account_id for row in captures}):
        raise ValueError("V4 terminal account membership differs from run context")
    if (broker_snapshots is None
            or len(broker_snapshots.accounts) != len(context["account_ids"])
            or {row["account_id"] for row in broker_snapshots.accounts}
               != set(context["account_ids"])):
        raise ValueError("V4 terminal broker evidence differs from run accounts")
    _publish_typed_batch_v4(client, batch,
                            broker_snapshot_rows=broker_snapshots)
    prefix = (load_verified_v4_prefix(client, batch.run_id)
              if first_price_source is None else load_verified_v4_prefix(
                  client, batch.run_id, first_price_source=first_price_source))
    if (prefix is None or prefix.status != batch.status
            or prefix.last_batch_id != batch.batch_id
            or prefix.last_sequence != batch.last_sequence):
        raise RuntimeError("V4 terminal commit lacks exact cold readback")
    _publish_terminal_snapshots_after_verified_prefix(
        client, prefix, captures, context)
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
            or row["strategy_number"] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30)
            or row["boundary_ms"] != boundary_ms
            or elapsed.microseconds % 1_000
            or Decimal(str(row["frozen_gap"])) <= 0
            or not 0 < row["episode_start_ms"] <= boundary_ms
            or not 0 < row["bos_break_boundary_ms"] <= boundary_ms
            or not row["assignment_id"] or not row["target_level_id"]
            or not row["bos_support_level_id"]):
        raise ValueError("V4 Strategy 1 entry evidence differs from its typed parent")
    if row["strategy_number"] in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        from .strategy_recent_bos_entry import recent_bos_entry
        if not recent_bos_entry(boundary_ms=row["boundary_ms"],
                                bos_break_boundary_ms=row["bos_break_boundary_ms"]):
            raise ValueError("Strategy 12 normalized entry requires recent supported BOS")



def _sealed_strategy_one_add_rows(batch, base_families, source_rows):
    """Require one scalar child for every numbered add intent in the batch."""
    from src.trading_runtime.arte_journal_writer import typed_row

    parents = {str(UUID(str(row["record_id"]))): row for name, rows in base_families
               if name == "trading_strategy_intent_v1" for row in rows
               if row["reason"] == "strategy_one_add"}
    events = {str(UUID(str(row["record_id"]))): row for name, rows in base_families
              if name == "trading_event_v1" for row in rows}
    if len(source_rows) != len(parents):
        raise ValueError("V4 Strategy 1 add intent lacks exact normalized evidence")
    seen = set()
    sealed = []
    for source in source_rows:
        row = typed_row(ADD_EVIDENCE.name, source)
        parent_id = str(UUID(str(row["parent_record_id"])))
        parent = parents.get(parent_id)
        event = events.get(parent_id)
        if parent_id in seen or parent is None or event is None:
            raise ValueError("V4 Strategy 1 add evidence has no unique parent intent")
        seen.add(parent_id)
        _validate_strategy_one_add_link(row, parent, event,
                                        batch.run_id, batch.batch_id)
        sealed.append(row)
    return tuple(sealed)


def _validate_strategy_one_add_link(row, parent, event, run_id, batch_id):
    """Verify scalar add facts against the independently typed parent clock."""
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
            or parent["action"] != "add_long"
            or parent["protection_profile_id"]
               != "early-squeeze-fixed-stop-full-target"
            or row["strategy_number"] not in (1, 2, 3)
            or row["boundary_ms"] != boundary_ms
            or boundary_ms <= 0 or boundary_ms % 1_000
            or elapsed.microseconds % 1_000
            or Decimal(str(row["resistance_midpoint"])) <= 0
            or not row["assignment_id"] or not row["resistance_id"]
            or row["purchase_ordinal"] not in (2, 3)):
        raise ValueError("V4 Strategy 1 add evidence differs from its typed parent")


def _publish_typed_batch_v4(client, batch, *, followthrough_rows=(), strategy_one_entry_rows=(),
                           rising_momentum_rows=(), initial_momentum_rows=(),
                           first_price_rows=(), first_price_authorities=(),
                            strategy_one_add_rows=(),
                            oms_tactic_rows=None,
                            portfolio_allocation_row=None,
                            reservation_reason_rows=(),
                            broker_acknowledgement_row=None,
                            broker_acknowledgement_v5_row=None,
                            order_cancel_row=None,
                            order_reprice_row=None,
                            order_modify_command_row=None,
                            risk_action_row=None,
                            risk_reply_rows=(),
                            protection_change_row=None,
                            protection_entry_order_rows=(),
                            protection_reconciliation_row=None,
                            protection_reconciliation_actions=(),
                            protection_reconciliation_replies=(),
                            broker_snapshot_rows=None,
                            _prepare_only=False):
    from src.trading_runtime.arte_journal_writer import (
        TypedJournalBatch, _CONTRACTS, _identity, _insert, _literal, _rows,
        _sealed_families, _v4_family_table, _verify_commission_links, typed_row,
        _verify_exact_intent_uses, _verify_order_context_links,
    )
    from src.trading_runtime.arte_journal_schema import V4_ORDER_COMMAND_LINEAGE
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (type(_prepare_only) is not bool
            or not isinstance(batch, TypedJournalBatch)
            or not 1 <= len(batch.events) <= MAX_V4_COMMIT_EVENTS
            or batch.status not in {"running", "completed", "stopped", "failed"}):
        raise ValueError("V4 publication needs one bounded typed event batch")
    if (getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)):
        raise RuntimeError("V4 publication requires a strict Keeper-fenced insert dispatch")
    live_lease = getattr(client, "live_v4_lease", None)
    if live_lease is not None:
        if live_lease.run_id != batch.run_id:
            raise RuntimeError("Live V4 publication crossed its Keeper run")
        live_lease.assert_current()
        if (any(getattr(batch, name) for name in (
                "backtest_cursors", "backtest_market_authorities",
                "backtest_progress", "prepared_v7_leases"))
                or broker_snapshot_rows is not None):
            raise ValueError("Live V4 cannot publish Backtest-only families")
    dispatch = client.typed_insert_dispatch
    if sum(bool(value) for value in (
            strategy_one_entry_rows, followthrough_rows, portfolio_allocation_row,
            oms_tactic_rows,
            reservation_reason_rows,
            broker_acknowledgement_row, broker_acknowledgement_v5_row,
            order_cancel_row,
            order_reprice_row,
            order_modify_command_row,
            risk_action_row,
            protection_change_row, protection_reconciliation_row,
            broker_snapshot_rows)) > 1:
        raise ValueError("V4 batch cannot mix independent typed supplements")
    snapshot_accounts = ()
    snapshot_positions = ()
    if broker_snapshot_rows is not None:
        from src.backend.backtest_terminal_broker_snapshot_v4 import V4BrokerSnapshotRows
        from src.backend.backtest_terminal_snapshot_v2 import recover_snapshot_group

        if (type(broker_snapshot_rows) is not V4BrokerSnapshotRows
                or batch.status not in {"completed", "stopped", "failed"}
                or broker_snapshot_rows.first_sequence != batch.first_sequence
                or broker_snapshot_rows.last_sequence != batch.last_sequence
                or len(batch.events) < 2):
            raise ValueError("V4 broker snapshots require the full terminal suffix")
        snapshot_accounts = tuple(typed_row(
            "trading_backtest_account_snapshot_v2",
            {key: value for key, value in row.items() if key != "content_hash"})
            for row in broker_snapshot_rows.accounts)
        snapshot_positions = tuple(typed_row(
            "trading_backtest_position_snapshot_v2",
            {key: value for key, value in row.items() if key != "content_hash"})
            for row in broker_snapshot_rows.positions)
        if (not snapshot_accounts
                or any(source["content_hash"] != sealed["content_hash"]
                       for source, sealed in zip(broker_snapshot_rows.accounts,
                                                 snapshot_accounts))
                or any(source["content_hash"] != sealed["content_hash"]
                       for source, sealed in zip(broker_snapshot_rows.positions,
                                                 snapshot_positions))):
            raise ValueError("V4 broker snapshot row differs from its Float64 seal")
        events = {str(UUID(str(row["record_id"]))): row for row in batch.events}
        if (len(events) != len(batch.events)
                or set(events) != {str(UUID(str(row["record_id"]))) for row in
                                    (*snapshot_accounts, *snapshot_positions,
                                     *batch.run_transitions)}):
            raise ValueError("V4 broker snapshot events lack exact typed coverage")
        for row in snapshot_accounts:
            event = events.get(str(UUID(str(row["record_id"]))))
            if (event is None or (event["category"], event["entity_type"])
                    != ("snapshot", "portfolio")
                    or event["entity_id"] != row["account_id"]
                    or event["account_id"] != row["account_id"]):
                raise ValueError("V4 broker account differs from its event")
            children = tuple(child for child in snapshot_positions
                             if child["parent_snapshot_id"] == row["snapshot_id"])
            recover_snapshot_group(row, children)
        for row in snapshot_positions:
            event = events.get(str(UUID(str(row["record_id"]))))
            if (event is None or (event["category"], event["entity_type"])
                    != ("snapshot", "position")
                    or event["entity_id"] != str(row["conid"])
                    or event["account_id"] != row["account_id"]
                    or row["parent_snapshot_id"] not in {
                        account["snapshot_id"] for account in snapshot_accounts}):
                raise ValueError("V4 broker position differs from its event")
    ack_rows = ()
    if broker_acknowledgement_row is not None:
        if (strategy_one_entry_rows or len(batch.events) != 1
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                != ("broker", "order_acknowledgement")
                or not isinstance(broker_acknowledgement_row, Mapping)):
            raise ValueError("V4 broker acknowledgement has an invalid event envelope")
        ack = typed_row(ACKNOWLEDGEMENT.name, {
            key: value for key, value in broker_acknowledgement_row.items()
            if key != "content_hash"})
        if ("content_hash" in broker_acknowledgement_row
                and ack["content_hash"] != broker_acknowledgement_row["content_hash"]):
            raise ValueError("V4 broker acknowledgement content differs from its seal")
        event = batch.events[0]
        if (str(UUID(str(ack["record_id"]))) != str(UUID(str(event["record_id"])))
                or ack["run_id"] != batch.run_id
                or str(UUID(str(ack["batch_id"]))) != batch.batch_id
                or ack["event_month"] != event["event_month"]
                or ack["broker_order_id"] != event["entity_id"]
                or ack["ticker"] != ack["ticker"].upper()):
            raise ValueError("V4 broker acknowledgement differs from its parent")
        ack_rows = (ack,)
    ack_v5_rows = ()
    if broker_acknowledgement_v5_row is not None:
        if (len(batch.events) != 1
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   != ("broker", "order_acknowledgement")
                or not isinstance(broker_acknowledgement_v5_row, Mapping)):
            raise ValueError("V5 broker acknowledgement has an invalid event envelope")
        ack_v5 = typed_row(ACKNOWLEDGEMENT_V5.name, {
            key: value for key, value in broker_acknowledgement_v5_row.items()
            if key != "content_hash"})
        event = batch.events[0]
        if ("content_hash" in broker_acknowledgement_v5_row
                and ack_v5["content_hash"] !=
                    broker_acknowledgement_v5_row["content_hash"]
                or str(UUID(str(ack_v5["record_id"]))) !=
                   str(UUID(str(event["record_id"])))
                or ack_v5["run_id"] != batch.run_id
                or str(UUID(str(ack_v5["batch_id"]))) != batch.batch_id
                or ack_v5["event_month"] != event["event_month"]
                or ack_v5["broker_order_id"] != event["entity_id"]):
            raise ValueError("V5 broker acknowledgement differs from its parent")
        ack_v5_rows = (ack_v5,)
    cancel_rows = ()
    if order_cancel_row is not None:
        if (len(batch.events) != 1 or not isinstance(order_cancel_row, Mapping)
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   not in {("command", "order_cancel"),
                           ("broker", "order_cancel_requested")}):
            raise ValueError("V4 cancellation has an invalid event envelope")
        cancel = typed_row(CANCEL.name, {
            key: value for key, value in order_cancel_row.items()
            if key != "content_hash"})
        event = batch.events[0]
        if ("content_hash" in order_cancel_row
                and cancel["content_hash"] != order_cancel_row["content_hash"]
                or str(UUID(str(cancel["record_id"])))
                   != str(UUID(str(event["record_id"])))
                or cancel["run_id"] != batch.run_id
                or str(UUID(str(cancel["batch_id"]))) != batch.batch_id
                or cancel["event_month"] != event["event_month"]
                or cancel["broker_order_id"] != event["entity_id"]
                or (event["category"] == "command")
                   != (cancel["result_kind"] == "command")):
            raise ValueError("V4 cancellation differs from its parent")
        cancel_rows = (cancel,)
    reprice_rows = ()
    if order_reprice_row is not None:
        if (len(batch.events) != 1 or not isinstance(order_reprice_row, Mapping)
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   not in {("broker", "order_repriced"),
                           ("broker", "order_reprice_error")}):
            raise ValueError("V4 repricing has an invalid event envelope")
        reprice = typed_row(REPRICE.name, {
            key: value for key, value in order_reprice_row.items()
            if key != "content_hash"})
        event = batch.events[0]
        if ("content_hash" in order_reprice_row
                and reprice["content_hash"] != order_reprice_row["content_hash"]
                or str(UUID(str(reprice["record_id"])))
                   != str(UUID(str(event["record_id"])))
                or reprice["run_id"] != batch.run_id
                or str(UUID(str(reprice["batch_id"]))) != batch.batch_id
                or reprice["event_month"] != event["event_month"]
                or reprice["broker_order_id"] != event["entity_id"]
                or (event["entity_type"] == "order_repriced")
                   != (reprice["result_kind"] == "modified")):
            raise ValueError("V4 repricing differs from its parent")
        reprice_rows = (reprice,)
    modify_command_rows = ()
    if order_modify_command_row is not None:
        if (live_lease is None or len(batch.events) != 1
                or not isinstance(order_modify_command_row, Mapping)
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   != ("command", "order_modify")):
            raise ValueError("V4 modification lacks a live command envelope")
        modified = typed_row(MODIFY_COMMAND.name, {
            key: value for key, value in order_modify_command_row.items()
            if key != "content_hash"})
        event = batch.events[0]
        if ("content_hash" in order_modify_command_row
                and modified["content_hash"] != order_modify_command_row["content_hash"]
                or str(UUID(str(modified["record_id"])))
                   != str(UUID(str(event["record_id"])))
                or modified["run_id"] != batch.run_id
                or str(UUID(str(modified["batch_id"]))) != batch.batch_id
                or modified["event_month"] != event["event_month"]
                or modified["account_id"] != event["account_id"]
                or modified["broker_order_id"] != event["entity_id"]
                or not _same_utc_time(modified["requested_at"], event["event_time"])
                or modified["intent_id"] != event["causation_id"]):
            raise ValueError("V4 modification differs from its typed event")
        modify_command_rows = (modified,)
    risk_rows = ()
    risk_replies = ()
    if risk_action_row is not None:
        if (len(batch.events) != 1 or not isinstance(risk_action_row, Mapping)
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   not in {("risk", "kill_entry_order"),
                           ("risk", "emergency_flatten")}):
            raise ValueError("V4 risk action has an invalid event envelope")
        risk_rows = (typed_row(RISK_ACTION.name, {
            key: value for key, value in risk_action_row.items()
            if key != "content_hash"}),)
        risk_replies = tuple(typed_row(RISK_REPLY.name, {
            key: value for key, value in source.items()
            if key != "content_hash"}) for source in risk_reply_rows)
        if ("content_hash" in risk_action_row
                and risk_rows[0]["content_hash"] != risk_action_row["content_hash"]
                or any("content_hash" in source
                       and source["content_hash"] != child["content_hash"]
                       for source, child in zip(risk_reply_rows, risk_replies))):
            raise ValueError("V4 risk action differs from its scalar seal")
        seal_risk_action_v4(
            risk_rows, risk_replies, batch.events,
            run_id=batch.run_id, batch_id=batch.batch_id)
    elif risk_reply_rows:
        raise ValueError("V4 risk replies lack their action parent")
    protection_rows = ()
    protection_children = ()
    if protection_change_row is not None:
        if (len(batch.events) != 1
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                   != ("protection", "protection_change")
                or not isinstance(protection_change_row, Mapping)):
            raise ValueError("V4 protection change has an invalid parent event")
        protection_rows = (typed_row(PROTECTION_CHANGE.name, {
            key: value for key, value in protection_change_row.items()
            if key != "content_hash"}),)
        if ("content_hash" in protection_change_row
                and protection_change_row["content_hash"]
                    != protection_rows[0]["content_hash"]):
            raise ValueError("V4 protection change content differs from its seal")
        protection_children = tuple(typed_row(PROTECTION_ENTRY_ORDER.name, {
            key: value for key, value in source.items()
            if key != "content_hash"}) for source in protection_entry_order_rows)
        if any("content_hash" in source
               and source["content_hash"] != child["content_hash"]
               for source, child in zip(protection_entry_order_rows,
                                        protection_children)):
            raise ValueError("V4 protection entry order differs from its seal")
        seal_protection_changes_v3(
            protection_rows, protection_children, batch.events,
            run_id=batch.run_id, batch_id=batch.batch_id)
    reconciliation_rows = ()
    reconciliation_actions = ()
    reconciliation_replies = ()
    if protection_reconciliation_row is not None:
        if (len(batch.events) != 1 or not isinstance(protection_reconciliation_row, Mapping)
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                != ("order_management", "protection_reconciliation")):
            raise ValueError("V4 protection reconciliation has an invalid parent")
        def seal_row(name, source):
            if not isinstance(source, Mapping):
                raise ValueError("V4 reconciliation child is not a typed row")
            row = typed_row(name, {key: value for key, value in source.items()
                                   if key != "content_hash"})
            if "content_hash" in source and source["content_hash"] != row["content_hash"]:
                raise ValueError("V4 reconciliation child differs from its seal")
            return row
        reconciliation_rows = (seal_row(PROTECTION_RECONCILIATION.name,
                                         protection_reconciliation_row),)
        reconciliation_actions = tuple(seal_row(RECONCILIATION_ACTION.name, row)
                                       for row in protection_reconciliation_actions)
        reconciliation_replies = tuple(seal_row(RECONCILIATION_REPLY.name, row)
                                       for row in protection_reconciliation_replies)
        seal_protection_reconciliation_v4(
            reconciliation_rows, reconciliation_actions, reconciliation_replies,
            batch.events, run_id=batch.run_id, batch_id=batch.batch_id)
    elif protection_reconciliation_actions or protection_reconciliation_replies:
        raise ValueError("V4 reconciliation children lack their typed parent")
    allocation_rows = ()
    if portfolio_allocation_row is not None:
        if (len(batch.events) != 1
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                != ("portfolio_management", "portfolio_allocation")
                or not isinstance(portfolio_allocation_row, Mapping)):
            raise ValueError("V4 allocation has an invalid parent event")
        allocation = typed_row(V4_ALLOCATION.name, {
            key: value for key, value in portfolio_allocation_row.items()
            if key != "content_hash"})
        if ("content_hash" in portfolio_allocation_row
                and allocation["content_hash"]
                != portfolio_allocation_row["content_hash"]):
            raise ValueError("V4 allocation differs from its scalar seal")
        seal_portfolio_allocation_v3(
            (allocation,), batch.events, run_id=batch.run_id,
            batch_id=batch.batch_id)
        allocation_rows = (allocation,)
    reason_rows = tuple(typed_row(RESERVATION_REASON.name, {
        key: value for key, value in row.items() if key != "content_hash"})
        for row in reservation_reason_rows)
    if reason_rows:
        if (len(batch.events) != 1
                or (batch.events[0]["category"], batch.events[0]["entity_type"])
                != ("portfolio_management", "portfolio_reservation")):
            raise ValueError("V4 reservation reasons lack one typed parent")
        seal_reservation_reason_family_v3(
            reason_rows, batch.events, batch.portfolio_reservation_events,
            run_id=batch.run_id, batch_id=batch.batch_id)
    base_families = _sealed_families(
        batch, v4_broker_ack_ids=tuple(row["record_id"] for row in ack_rows),
        v5_broker_ack_ids=tuple(row["record_id"] for row in ack_v5_rows),
        v4_allocation_ids=tuple(row["record_id"] for row in allocation_rows),
        v4_order_cancel_ids=tuple(row["record_id"] for row in cancel_rows),
        v4_order_reprice_ids=tuple(row["record_id"] for row in reprice_rows),
        v4_order_modify_ids=tuple(row["record_id"] for row in modify_command_rows),
        v4_risk_action_ids=tuple(row["record_id"] for row in risk_rows),
        v4_protection_ids=tuple(row["record_id"] for row in protection_rows),
        v4_reconciliation_ids=tuple(row["record_id"] for row in reconciliation_rows),
        v4_snapshot_account_ids=tuple(row["record_id"] for row in snapshot_accounts),
        v4_snapshot_position_ids=tuple(row["record_id"] for row in snapshot_positions))
    command_rows = dict(base_families)["trading_order_command_v1"]
    strategy_one_commands = {
        str(UUID(str(row["record_id"]))) for row in command_rows
        if str(row["strategy_id"]) == "early-squeeze-strategy"
        and int(row["strategy_revision"]) in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30)
    }
    lineage_rows = tuple(typed_row(V4_ORDER_COMMAND_LINEAGE.name, row)
                         for row in batch.v4_command_lineages)
    lineage_parents = {str(UUID(str(row["parent_record_id"])))
                       for row in lineage_rows}
    commands_by_id = {str(UUID(str(row["record_id"]))): row
                      for row in command_rows}
    if (len(lineage_parents) != len(lineage_rows)
            or lineage_parents != strategy_one_commands
            or any(str(row["run_id"]) != batch.run_id
                   or str(UUID(str(row["batch_id"]))) != batch.batch_id
                   or row["account_id"] != commands_by_id[str(row["parent_record_id"])]["account_id"]
                   or row["event_month"] != commands_by_id[str(row["parent_record_id"])]["event_month"]
                   or (row["lineage_kind"], bool(row["oms_group_record_id"]),
                       bool(row["proof_record_id"]))
                   not in {("initial_intent", False, False),
                           ("oms_group", True, False),
                           ("oms_target_amendment", True, True)}
                   for row in lineage_rows)):
        raise ValueError("V4 Strategy 1 commands need one typed lineage row each")
    entry_rows = _sealed_strategy_one_entry_rows(
        batch, base_families, strategy_one_entry_rows)
    momentum_rows = seal_rising_momentum_rows(rising_momentum_rows, entry_rows,
        dict(base_families)["trading_strategy_intent_v1"],
        dict(base_families)["trading_event_v1"])
    initial_rows = seal_initial_momentum_rows(initial_momentum_rows, entry_rows,
        dict(base_families)["trading_strategy_intent_v1"],
        dict(base_families)["trading_event_v1"], momentum_rows)
    price_rows = seal_first_price_rows(first_price_rows, entry_rows,
        dict(base_families)["trading_strategy_intent_v1"],
        dict(base_families)["trading_event_v1"], first_price_authorities)
    add_rows = _sealed_strategy_one_add_rows(
        batch, base_families, strategy_one_add_rows)
    # Compound micro-preparation cannot look up an entry in a sibling unit
    # before publication. Its controller seals the complete merged graph below.
    failure_rows = (tuple(typed_row(FAILURE.name, {k: v for k, v in row.items()
                          if k != "content_hash"}) for row in followthrough_rows)
                    if _prepare_only else seal_followthrough_rows(
                        client, followthrough_rows,
                        dict(base_families)["trading_strategy_intent_v1"],
                        dict(base_families)["trading_event_v1"], entry_rows,
                        prior_batch_id=batch.prior_batch_id))
    tactic_states = ()
    tactic_steps = ()
    if oms_tactic_rows is not None:
        from .arte_oms_tactic_projection import (
            PARENT_TABLE, STEP_TABLE, seal_oms_tactic_rows,
        )

        if (len(batch.events) != 1 or len(batch.oms_group_states) != 1
                or not isinstance(oms_tactic_rows, tuple)
                or len(oms_tactic_rows) != 2):
            raise ValueError("V4 OMS tactic needs one group revision")
        parent_source, step_sources = oms_tactic_rows
        tactic_states = (typed_row(PARENT_TABLE, {
            key: value for key, value in parent_source.items()
            if key != "content_hash"}),)
        tactic_steps = tuple(typed_row(STEP_TABLE, {
            key: value for key, value in source.items()
            if key != "content_hash"}) for source in step_sources)
        if ("content_hash" in parent_source
                and tactic_states[0]["content_hash"] != parent_source["content_hash"]
                or any("content_hash" in source
                       and source["content_hash"] != sealed["content_hash"]
                       for source, sealed in zip(step_sources, tactic_steps))):
            raise ValueError("V4 OMS tactic differs from its scalar seal")
        seal_oms_tactic_rows(
            tactic_states, tactic_steps,
            dict(base_families)["trading_oms_group_state_v1"],
            dict(base_families)["trading_event_v1"],
            run_id=batch.run_id, batch_id=batch.batch_id)
    families = tuple((_v4_family_table(name), rows)
                     for name, rows in base_families)
    if lineage_rows:
        families += ((V4_ORDER_COMMAND_LINEAGE.name, lineage_rows),)
    if tactic_states:
        families += ((PARENT_TABLE, tactic_states),)
    if tactic_steps:
        families += ((STEP_TABLE, tactic_steps),)
    if failure_rows:
        families += ((FAILURE.name, failure_rows),)
    if entry_rows:
        families += ((ENTRY_EVIDENCE.name, entry_rows),)
    if momentum_rows:
        families += ((MOMENTUM.name, momentum_rows),)
    if initial_rows:
        families += ((INITIAL_MOMENTUM.name, initial_rows),)
    if price_rows:
        families += ((FIRST_PRICE.name, price_rows),)
    if add_rows:
        families += ((ADD_EVIDENCE.name, add_rows),)
    if allocation_rows:
        families += ((V4_ALLOCATION.name, allocation_rows),)
    if reason_rows:
        families += ((RESERVATION_REASON.name, reason_rows),)
    if ack_rows:
        families += ((ACKNOWLEDGEMENT.name, ack_rows),)
    if ack_v5_rows:
        families += ((ACKNOWLEDGEMENT_V5.name, ack_v5_rows),)
    if cancel_rows:
        families += ((CANCEL.name, cancel_rows),)
    if reprice_rows:
        families += ((REPRICE.name, reprice_rows),)
    if modify_command_rows:
        families += ((MODIFY_COMMAND.name, modify_command_rows),)
    if risk_rows:
        families += ((RISK_ACTION.name, risk_rows),
                     (RISK_REPLY.name, risk_replies))
    if protection_rows:
        families += ((PROTECTION_CHANGE.name, protection_rows),)
    if protection_children:
        families += ((PROTECTION_ENTRY_ORDER.name, protection_children),)
    if reconciliation_rows:
        families += ((PROTECTION_RECONCILIATION.name, reconciliation_rows),)
    if reconciliation_actions:
        families += ((RECONCILIATION_ACTION.name, reconciliation_actions),)
    if reconciliation_replies:
        families += ((RECONCILIATION_REPLY.name, reconciliation_replies),)
    if snapshot_accounts:
        families += (("trading_backtest_account_snapshot_v2", snapshot_accounts),)
    if snapshot_positions:
        families += (("trading_backtest_position_snapshot_v2", snapshot_positions),)
    if _prepare_only:
        # The compound transport rekeys these fully validated normalized
        # families before one Keeper reservation. No query or INSERT has run.
        return base_families, families
    return _publish_sealed_batch_v4(client, batch, base_families, families,
        first_price_authorities=first_price_authorities)


def _existing_detail_identities_v4(client, batch, families):
    """Read all pre-insert family identities in one bounded SELECT on ClickHouse."""
    from research.mlops.clickhouse import ClickHouseHttpClient
    from src.trading_runtime.arte_journal_writer import _CONTRACTS, _literal, _rows

    present = [(name, rows) for name, rows in families if rows]
    if not present:
        return {}
    if any(name not in _CONTRACTS or not 1 <= len(rows) <= 65_536
           for name, rows in present):
        raise ValueError("V4 detail existence scope is unbounded or untyped")
    filters = (f"WHERE run_id={_literal(batch.run_id)} "
               f"AND batch_id=toUUID({_literal(batch.batch_id)}) ")
    if not isinstance(client, ClickHouseHttpClient) or len(present) == 1:
        return {name: sorted((str(UUID(str(row["record_id"]))),
                              str(row["content_hash"])) for row in _rows(
            client, f"SELECT record_id,content_hash FROM arte.{name} "
                    f"{filters}FORMAT JSONEachRow")) for name, _ in present}
    branches = [
        f"(SELECT {_literal(name)} AS family_name,"
        "toString(record_id) AS record_id,content_hash "
        f"FROM arte.{name} {filters}LIMIT {len(rows) + 1})"
        for name, rows in present
    ]
    observed = _rows(client, " UNION ALL ".join(branches) + " FORMAT JSONEachRow")
    by_name = {name: [] for name, _ in present}
    for row in observed:
        if set(row) != {"family_name", "record_id", "content_hash"}:
            raise RuntimeError("V4 typed detail inventory has unexpected columns")
        name = row["family_name"]
        if name not in by_name:
            raise RuntimeError("V4 typed detail inventory has a foreign family")
        by_name[name].append((str(UUID(str(row["record_id"]))),
                              str(row["content_hash"])))
    return {name: sorted(identities) for name, identities in by_name.items()}


def _insert_detail_families_v4(client, batch, pending):
    """Insert independent detail families in bounded lanes, before any commit.

    Every lane has its own HTTP connection. The shared Keeper dispatch registers
    each exact INSERT; a failed lane leaves the batch uncommitted and fenced.
    The caller still performs complete typed readback before publishing a
    family set or cursor. Fake and older clients retain the serial path.
    """
    from src.trading_runtime.arte_journal_writer import _insert

    factory = getattr(client, "v4_insert_lane_factory", None)
    lane_limit = getattr(client, "v4_insert_lane_limit", 1)
    cached_lanes = getattr(client, "v4_insert_lane_cache", None)
    if factory is None or len(pending) < 2:
        for name, rows in pending:
            _insert(client, name, tuple(rows), f"{batch.batch_id}:{name}:v4",
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
        return
    if (type(lane_limit) is not int or not 2 <= lane_limit <= 4
            or not callable(factory)):
        raise ValueError("V4 detail INSERT lanes must be bounded and configured")
    lane_count = min(lane_limit, len(pending))
    lanes = []
    borrowed = cached_lanes is not None
    try:
        if borrowed and (not isinstance(cached_lanes, tuple)
                         or len(cached_lanes) != lane_limit):
            raise ValueError("V4 cached detail lanes differ from their bound")
        for index in range(lane_count):
            lane = cached_lanes[index] if borrowed else factory()
            if (lane is client or any(lane is prior for prior in lanes)
                    or getattr(lane, "typed_insert_strict", False) is not True
                    or getattr(lane, "typed_insert_dispatch", None)
                    is not client.typed_insert_dispatch):
                if (not borrowed and lane is not client
                        and all(lane is not prior for prior in lanes)):
                    lane.close()
                raise RuntimeError("V4 detail lane lacks an independent fenced client")
            lanes.append(lane)

        def publish_lane(lane, work):
            for name, rows in work:
                _insert(lane, name, tuple(rows), f"{batch.batch_id}:{name}:v4",
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)

        groups = tuple(tuple(pending[index::lane_count])
                       for index in range(lane_count))
        with ThreadPoolExecutor(max_workers=lane_count,
                                thread_name_prefix="v4-detail-insert") as pool:
            futures = tuple(pool.submit(publish_lane, lane, work)
                            for lane, work in zip(lanes, groups, strict=True))
            errors = []
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]
    finally:
        if not borrowed:
            for lane in lanes:
                lane.close()


def _publish_sealed_batch_v4(client, batch, base_families, families, *,
                             timings_ns: dict[str, int] | None = None,
                             first_price_authorities: tuple = ()) -> str:
    """Publish one sealed normalized family graph under a Keeper fence."""
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _identity, _insert, _literal, _rows,
        _verify_commission_links, _verify_exact_intent_uses,
        _verify_order_context_links,
    )

    stage_started = perf_counter_ns()

    def mark_stage(name: str) -> None:
        nonlocal stage_started
        finished = perf_counter_ns()
        if timings_ns is not None:
            timings_ns[f"stage_{name}"] = finished - stage_started
        stage_started = finished

    live_lease = getattr(client, "live_v4_lease", None)
    dispatch = client.typed_insert_dispatch
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
            client, run_id=batch.run_id, batch_id=batch.batch_id,
            first_price_authorities=first_price_authorities)
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
    profile = "live_v4" if live_lease is not None else "backtest_v4"
    _verify_commission_links(
        client, batch, base_families, journal_profile=profile)
    _verify_exact_intent_uses(
        client, batch, base_families, journal_profile=profile)
    _verify_order_context_links(
        client, batch, base_families, journal_profile=profile)
    dispatch.assert_next_batch(
        run_id=batch.run_id, batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id,
        first_sequence=batch.first_sequence,
        last_sequence=batch.last_sequence)
    mark_stage("prechecks")

    existing_identities = _existing_detail_identities_v4(client, batch, families)
    mark_stage("detail_inventory")
    pending = []
    for name, rows in families:
        if not rows:
            continue
        identities = existing_identities[name]
        expected = _identity(rows)
        if identities and identities != expected:
            raise RuntimeError("V4 typed detail conflicts with a prior attempt")
        if not identities:
            pending.append((name, rows))
    _insert_detail_families_v4(client, batch, pending)
    mark_stage("detail_insert")
    actual_details = _load_verified_details_v4(
        client, run_id=batch.run_id, batch_id=batch.batch_id,
        family_rows=family_rows, max_rows_per_family=65_536,
        batched_readback=bool(getattr(client, "v4_batched_detail_readback", False)),
        prior_batch_id=batch.prior_batch_id,
        first_price_authorities=first_price_authorities)
    verify_commit_v4(commit, family_rows, actual_details)
    mark_stage("detail_readback")

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
    mode = _family_operation_mode(dispatch, batch, family_rows)
    for row in family_rows:
        prior = by_name.get(row["family_name"])
        if prior is not None and prior != row:
            raise RuntimeError("V4 family publication conflicts with a prior attempt")
    if mode == "grouped":
        if not existing_families:
            _insert(client, "trading_commit_family_v4", tuple(family_rows),
                    f"{batch.batch_id}:family-set:v4",
                    dispatch_batch_id=batch.batch_id,
                    dispatch_sequence=batch.last_sequence)
    else:
        for row in family_rows:
            if row["family_name"] not in by_name:
                _insert(client, "trading_commit_family_v4", (row,),
                        f"{batch.batch_id}:family:{row['family_name']}",
                        dispatch_batch_id=batch.batch_id,
                        dispatch_sequence=batch.last_sequence)
    verified_families = _rows(client,
        f"SELECT {family_columns} FROM arte.trading_commit_family_v4 "
        f"{filters}LIMIT 257 FORMAT JSONEachRow")
    if sorted(verified_families, key=lambda row: row["family_name"]) != list(family_rows):
        raise RuntimeError("V4 family publication lacks complete readback")
    mark_stage("family_set")
    if live_lease is not None:
        live_lease.assert_current()
    _insert(client, "trading_commit_v4", (commit,),
            f"{batch.batch_id}:commit:v4", dispatch_batch_id=batch.batch_id,
            dispatch_sequence=batch.last_sequence)
    # Every detail family and the family set were independently read back and
    # sealed above. A second cold verification of all children here multiplies
    # ClickHouse round-trips per event without adding a new durability fact.
    # Recovery still runs load_verified_commit_v4 over the complete graph.
    loaded_rows = _rows(client,
        f"SELECT {commit_columns} FROM arte.trading_commit_v4 "
        f"{filters}LIMIT 2 FORMAT JSONEachRow")
    if len(loaded_rows) != 1:
        raise RuntimeError("V4 committed cursor is missing or ambiguous")
    loaded = loaded_rows[0]
    loaded_content = {key: value for key, value in loaded.items()
                      if key not in {"committed_at", "content_hash"}}
    if (loaded["run_id"] != batch.run_id
            or str(UUID(str(loaded["batch_id"]))) != batch.batch_id
            or sha256(canonical_json(loaded_content).encode()).hexdigest()
               != loaded["content_hash"]
            or loaded["content_hash"] != commit["content_hash"]):
        raise RuntimeError("V4 committed cursor differs from the intended batch")
    _compact_verified_v4_batch(
        dispatch, batch, families, family_rows, loaded)
    if live_lease is not None:
        live_lease.assert_current()
    mark_stage("commit_fence")
    return batch.batch_id
