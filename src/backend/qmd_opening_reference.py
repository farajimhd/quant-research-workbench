"""SELECT-only certification of the centrally published QMD opening basis.

Consumers read sealed scalar evidence. They cannot install tables, derive a
missing prior close, mutate the reference population or invoke a producer.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import json
import re

from pipelines.market_sip.events.market_day_sql import literal
from src.market_engine import opening_price_reference as c
from src.trading_runtime.arte_journal_schema import storage_preflight


def _rows(reader, query, bound):
    text = reader.execute(query + " FORMAT JSONEachRow")
    if not isinstance(text, str) or len(text.encode()) > 16 * 1024 * 1024:
        raise ValueError("QMD opening reference response exceeds its byte bound")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    if len(records) > bound:
        raise ValueError("QMD opening reference response exceeds its row bound")
    return records


def _permission(reader):
    user = reader.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("QMD opening reader identity is invalid")
    required = {("arte", t.name) for t in c.TABLES}
    required |= {("system", n) for n in (
        "storage_policies", "tables", "columns", "parts", "data_skipping_indices")}
    reads = set()
    for line in reader.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT SELECT ON ([A-Za-z_][A-Za-z0-9_]*)\."
            r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(3) != user:
            raise ValueError("QMD opening consumer must have explicit SELECT-only grants")
        reads.add(match.group(1, 2))
    if not required <= reads:
        raise ValueError("QMD opening reader lacks certified product grants")


def _scope(scope):
    return (f"source_build_id={literal(scope.build_id)} "
            f"AND session_date=toDate({literal(scope.session_date.isoformat())})")


def _seal(reader, scope):
    rows = _rows(reader, f"SELECT * FROM arte.{c.COVERAGE.name} WHERE {_scope(scope)} LIMIT 2", 2)
    if len(rows) != 1:
        raise ValueError("QMD opening reference coverage is missing or ambiguous")
    header = c.restore_record(c.COVERAGE, rows[0])
    expected = dict(source_build_id=scope.build_id, session_date=scope.session_date,
        reference_revision=c.REVISION, market_plan_hash=scope.market_plan_hash,
        prior_market_plan_hash=scope.prior_market_plan_hash, identity_plan_hash=scope.identity_plan_hash,
        population_reference_hash=scope.population_reference_hash, producer_code_hash=scope.producer_code_hash)
    if (any(header[k] != v for k, v in expected.items())
            or header["listing_count"] != len(scope.tickers)
            or header["resolved_count"] + header["covered_empty_count"]
                + header["excluded_identity_count"] != header["listing_count"]
            or header["split_count"] > 100000
            or header["content_hash"] != c.content_hash({k: v for k, v in header.items()
                if k not in {"content_hash", "certified_at"}})):
        raise ValueError("QMD opening reference coverage differs from the requested source pins")
    return header


@dataclass(frozen=True)
class CertifiedOpeningReference:
    scope: c.OpeningReferenceScope
    attempt_id: str
    content_hash: str
    rows_by_listing: Mapping[str, Mapping]

    def resolved_rows(self):
        return tuple(row for row in self.rows_by_listing.values() if row["status"] == "resolved")


def certify_opening_reference(reader, scope, *, expected_content_hash=None):
    """Rebuild hashes and split arithmetic from the normalized sealed evidence."""
    if type(scope) is not c.OpeningReferenceScope:
        raise TypeError("QMD opening reader requires its typed source scope")
    if expected_content_hash is not None:
        c.require_hash(expected_content_hash)
    _permission(reader)
    storage_preflight(reader, tables=c.TABLES)
    header = _seal(reader, scope)
    if expected_content_hash is not None and header["content_hash"] != expected_content_hash:
        raise ValueError("QMD opening reference differs from the pinned release/run product")
    attempt = header["reference_attempt_id"]
    predicate = _scope(scope) + f" AND reference_attempt_id=toUUID({literal(attempt)})"
    identities = _rows(reader, f"SELECT listing_id,ticker FROM arte.{c.REFERENCE.name} "
        f"WHERE {predicate} ORDER BY listing_id LIMIT 25001", 25001)
    if (len(identities) != len(scope.tickers)
            or tuple(sorted(r["ticker"] for r in identities)) != scope.tickers
            or len({r["listing_id"] for r in identities}) != len(identities)):
        raise ValueError("QMD opening reference has missing, extra or duplicate population identities")
    references, children = [], []
    for offset in range(0, len(identities), 128):
        names = ",".join(literal(r["listing_id"]) for r in identities[offset:offset + 128])
        for table, target, bound, order in (
            (c.REFERENCE, references, 129, "listing_id"),
            (c.SPLIT, children, 4097, "listing_id,source_ordinal")):
            rows = _rows(reader, f"SELECT * FROM arte.{table.name} WHERE {predicate} "
                f"AND listing_id IN ({names}) ORDER BY {order} LIMIT {bound}", bound)
            if len(rows) >= bound:
                raise ValueError("QMD opening reference children exceed their certified bound")
            target.extend(c.restore_record(table, row) for row in rows)
            if len(children) > 100000:
                raise ValueError("QMD opening split evidence exceeds its population bound")
    references.sort(key=lambda row: row["listing_id"])
    children.sort(key=lambda row: (row["listing_id"], row["source_ordinal"]))
    for table, target, key in ((c.REFERENCE, references, "listing_id"),
                               (c.SPLIT, children, "(listing_id,source_ordinal)")):
        summary = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys "
            f"FROM arte.{table.name} WHERE {predicate}", 1)
        if (len(summary) != 1 or int(summary[0]["row_count"]) != len(target)
                or int(summary[0]["unique_keys"]) != len(target)):
            raise ValueError("QMD opening reference children contain extra or duplicate keys")
    if (len(references) != header["listing_count"] or len(children) != header["split_count"]
            or c.content_hash(tuple(references)) != header["reference_content_hash"]
            or c.content_hash(tuple(children)) != header["split_content_hash"]):
        raise ValueError("QMD opening reference child content hashes differ")
    splits_by_listing = {}
    listing_ids = {r["listing_id"] for r in identities}
    for row in children:
        if row["listing_id"] not in listing_ids:
            raise ValueError("QMD opening split evidence has a foreign identity")
        splits_by_listing.setdefault(row["listing_id"], []).append(row)
    regular = c.previous_regular_session(scope.session_date)
    restored = {}
    for row in references:
        if (row["source_build_id"] != scope.build_id or row["session_date"] != scope.session_date
                or row["reference_attempt_id"] != attempt or row["prior_session_date"] != regular.day
                or row["prior_regular_open_at"] != c.utc(regular.opened_at)
                or row["prior_regular_close_at"] != c.utc(regular.closed_at)):
            raise ValueError("QMD opening reference row crosses its source or exchange-session scope")
        split_rows = splits_by_listing.get(row["listing_id"], [])
        if [s["source_ordinal"] for s in split_rows] != list(range(len(split_rows))):
            raise ValueError("QMD opening split ordinals are not contiguous")
        splits = tuple(c.OpeningSplit(s["symbol_id"], s["listing_id"], s["security_id"],
            s["execution_date"], s["split_from"], s["split_to"], s["observed_at"],
            s["source_content_hash"]) for s in split_rows)
        listing = c.OpeningListing(row["listing_id"], row["ticker"], row["symbol_id"], row["security_id"],
            row["identity_interval_id"], row["identity_source_hash"], row["identity_valid_from"],
            row["identity_valid_to_exclusive"])
        projected, projected_splits = c.project_opening_reference(build_id=scope.build_id,
            day=scope.session_date, attempt_id=attempt, listing=listing, regular=regular,
            prior_build_id=row["prior_source_build_id"], prior_attempt_id=row["prior_source_attempt_id"],
            prior_unit_hash=row["prior_market_unit_hash"], prior_close_int=row["prior_close_int"], splits=splits)
        if projected != row or projected_splits != tuple(split_rows):
            raise ValueError("QMD opening reference split arithmetic or row evidence differs")
        restored[row["listing_id"]] = MappingProxyType(row)
    if (sum(r["status"] == "resolved" for r in references) != header["resolved_count"]
            or sum(r["status"] == "covered_empty" for r in references) != header["covered_empty_count"]
            or sum(r["status"] == "excluded_identity" for r in references) != header["excluded_identity_count"]
            or _seal(reader, scope) != header):
        raise ValueError("QMD opening reference coverage changed during certification")
    return CertifiedOpeningReference(scope, attempt, header["content_hash"], MappingProxyType(restored))


async def certify_opening_reference_async(reader_factory, scope, **pins):
    def run():
        reader = reader_factory()
        try:
            return certify_opening_reference(reader, scope, **pins)
        finally:
            reader.close()
    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
