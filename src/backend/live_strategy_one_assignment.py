"""Normalized Strategy 1 live assignment facts and an attested cold reader.

STRATEGY CREATION RULES: this table belongs only to immutable Strategy 1.
Changing its executable fields requires a new strategy number and a new
contract; never route numbered Strategy 1 through the legacy 26-47 parameter
or SQLite assignment snapshots. This reader never publishes rows or admits
orders. Plan membership, approved configuration, and activation are separate
Keeper-attested prerequisites.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol
from uuid import UUID

from src.backend.live_assignment_activation_join import AttestedPlanMembership
from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight
from src.trading_runtime.journal_contract import canonical_json


TABLE = TableContract(
    "strategy_one_live_assignment_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("session_date", "Date"), ("publication_id", "UUID"),
     ("run_plan_id", "String"),
     ("assignment_id", "String"), ("revision", "UInt64"),
     ("strategy_number", "UInt16"), ("account_id", "String"),
     ("ticker", "String"), ("conid", "UInt64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session_date)",
    "configuration_revision_id, session_date, publication_id, run_plan_id, assignment_id, revision",
)
_COLUMNS = tuple(name for name, _ in TABLE.columns)


def _identity(value: Any) -> str:
    if type(value) is not str or not value or any(char in value for char in "\r\n\x00"):
        raise ValueError("Strategy 1 assignment identity is invalid")
    return value


def _hash(content: Mapping[str, Any]) -> str:
    return sha256(canonical_json(content).encode()).hexdigest()


def _publication_id(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Strategy 1 assignment publication ID is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("Strategy 1 assignment publication ID is invalid") from exc
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError("Strategy 1 assignment publication ID is invalid")
    return value


def project_strategy_one_assignment(*, configuration_revision_id: str,
                                    session_date: str, publication_id: str,
                                    run_plan_id: str,
                                    assignment_id: str, revision: int,
                                    account_id: str, ticker: str,
                                    conid: int) -> dict[str, Any]:
    """Create one immutable, scalar row; membership pins its exact hash."""
    for value in (configuration_revision_id, run_plan_id, assignment_id,
                  account_id, ticker):
        _identity(value)
    _publication_id(publication_id)
    if (date.fromisoformat(session_date).isoformat() != session_date
            or type(revision) is not int or not 1 <= revision < 2**64
            or type(conid) is not int or not 1 <= conid < 2**64
            or ticker != ticker.upper()):
        raise ValueError("Strategy 1 assignment scope or instrument is invalid")
    content = dict(schema_version=1,
                   configuration_revision_id=configuration_revision_id,
                   session_date=session_date, publication_id=publication_id,
                   run_plan_id=run_plan_id,
                   assignment_id=assignment_id, revision=revision,
                   strategy_number=1, account_id=account_id,
                   ticker=ticker, conid=conid)
    return {**content, "content_hash": _hash(content)}


class MembershipProof(Protocol):
    def read_attested_plan(self, *, configuration_revision_id: str,
                           run_plan_id: str) -> AttestedPlanMembership: ...
    def head_hash(self, *, configuration_revision_id: str,
                  run_plan_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class StrategyOneLiveAssignment:
    assignment_id: str
    revision: int
    account_id: str
    ticker: str
    conid: int
    content_hash: str


def cold_read_strategy_one_assignments(
    client: Any, membership: MembershipProof, *,
    configuration_revision_id: str, session_date: str, run_plan_id: str,
    max_rows: int = 100_000,
) -> tuple[StrategyOneLiveAssignment, ...]:
    """Exact membership-to-fact join; reject absent, duplicate and orphan rows."""
    for value in (configuration_revision_id, run_plan_id):
        _identity(value)
    if (date.fromisoformat(session_date).isoformat() != session_date
            or type(max_rows) is not int or not 1 <= max_rows <= 100_000):
        raise ValueError("Strategy 1 assignment cold-read scope is invalid")
    pinned = membership.read_attested_plan(
        configuration_revision_id=configuration_revision_id,
        run_plan_id=run_plan_id)
    if (not isinstance(pinned, AttestedPlanMembership)
            or pinned.configuration_revision_id != configuration_revision_id
            or pinned.run_plan_id != run_plan_id):
        raise ValueError("Strategy 1 assignment membership scope differs")
    publication_id = _publication_id(pinned.publication_id)
    expected = {}
    for member in pinned.assignments:
        if member.run_plan_id != run_plan_id:
            continue
        if member.assignment_id in expected:
            raise ValueError("Strategy 1 membership repeats assignment")
        expected[member.assignment_id] = (member.base_sequence, member.base_hash)
    if len(expected) > max_rows:
        raise ValueError("Strategy 1 assignment membership exceeds cold bound")
    storage_preflight(client, tables=(TABLE,))
    literal = lambda value: "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    sql = (f"SELECT {','.join(_COLUMNS)} FROM arte.{TABLE.name} "
           f"WHERE configuration_revision_id={literal(configuration_revision_id)} "
           f"AND session_date=toDate({literal(session_date)}) "
           f"AND publication_id=toUUID({literal(publication_id)}) "
           f"AND run_plan_id={literal(run_plan_id)} "
           f"ORDER BY assignment_id,revision LIMIT {max_rows + 1} FORMAT JSONEachRow")
    rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    if len(rows) > max_rows or len(rows) != len(expected):
        raise ValueError("Strategy 1 assignment rows differ from attested membership")
    result = []
    seen = set()
    account_tickers = set()
    for row in rows:
        if type(row) is not dict or set(row) != set(_COLUMNS):
            raise ValueError("Strategy 1 assignment row columns differ")
        assignment_id = row["assignment_id"]
        if assignment_id in seen or assignment_id not in expected:
            raise ValueError("Strategy 1 assignment row is duplicate or unpinned")
        seen.add(assignment_id)
        canonical = project_strategy_one_assignment(
            configuration_revision_id=row["configuration_revision_id"],
            session_date=row["session_date"],
            publication_id=row["publication_id"], run_plan_id=row["run_plan_id"],
            assignment_id=assignment_id, revision=row["revision"],
            account_id=row["account_id"], ticker=row["ticker"],
            conid=row["conid"])
        if (row != canonical
                or row["configuration_revision_id"] != configuration_revision_id
                or row["session_date"] != session_date
                or row["publication_id"] != publication_id
                or row["run_plan_id"] != run_plan_id
                or expected[assignment_id] != (row["revision"], row["content_hash"])):
            raise ValueError("Strategy 1 assignment differs from pinned scalar fact")
        account_ticker = (row["account_id"], row["ticker"])
        if account_ticker in account_tickers:
            raise ValueError("Strategy 1 assignment repeats account and ticker")
        account_tickers.add(account_ticker)
        result.append(StrategyOneLiveAssignment(
            assignment_id, row["revision"], row["account_id"],
            row["ticker"], row["conid"], row["content_hash"]))
    assigned_tickers = {item.ticker for item in result}
    if any(watch.ticker not in assigned_tickers
           for watch in pinned.activated_watches):
        raise ValueError("Strategy 1 activated watch lacks a pinned assignment")
    if membership.head_hash(
            configuration_revision_id=configuration_revision_id,
            run_plan_id=run_plan_id) != pinned.head_hash:
        raise RuntimeError("Strategy 1 assignment membership head changed")
    return tuple(result)
