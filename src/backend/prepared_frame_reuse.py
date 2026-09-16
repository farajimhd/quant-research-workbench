"""Reuse certified completed streams without loading frame bodies into Python.

An identity sidecar preserves the *original* source query. Broader artifacts are
revalidated at that scope, never by comparing tokens from different requests.
Legacy artifacts without this evidence remain eligible for exact-key hits only.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


REVISION_FIELDS = ("token", "source_plan_hash", "calculation_revision", "corporate_action_revision")


async def joined_thread(function, *args, **kwargs):
    """Do not release the caller's cache lock while a cancelled disk write lives."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


def frame_identity(*, schema_version, start, end, requests, indicator_columns, source_revision):
    return {
        "schema_version": schema_version,
        "start": start.isoformat(), "end": end.isoformat(),
        "requests": [list(request) for request in sorted(requests)],
        "indicator_columns": list(indicator_columns) if indicator_columns is not None else None,
        "source_revision": {key: str((source_revision or {}).get(key) or "") for key in REVISION_FIELDS},
    }


def identity_name(identity):
    encoded = json.dumps(identity, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"v{identity['schema_version']}-{hashlib.sha256(encoded).hexdigest()}.sqlite3"


def register_identity(path: Path, identity: dict, *, warmup_days: int):
    if path.name != identity_name(identity):
        raise ValueError("Prepared frame identity does not match its artifact")
    sidecar = path.with_suffix(".identity.json")
    payload = {"identity": identity, "warmup_days": warmup_days}
    if sidecar.exists():
        if json.loads(sidecar.read_text(encoding="utf-8")) != payload:
            raise ValueError("Prepared frame identity metadata changed")
        return
    temporary = sidecar.with_name(f".{sidecar.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(sidecar)
    finally:
        temporary.unlink(missing_ok=True)


def register_legacy_identity(path: Path, run_summary: dict, *, schema_version: int) -> bool:
    """Explicit migration: promote only an exactly reconstructed legacy hash.

    Does not rewrite the database or bless inferred coverage. A run's recorded
    source identity, completed stream population and column projection must
    reproduce its original filename byte for byte. Reuse still revalidates QMD.
    """
    revision = run_summary.get("data_authority", {}).get("sources", {}).get("prepared_strategy_frame_source", {})
    if not revision.get("complete_for_history") or not revision.get("request_complete"):
        return False
    warmup_days = revision.get("indicator_warmup_days")
    if not isinstance(warmup_days, int):
        return False
    revision = dict(revision, token=revision.get("revision_token"))
    start = datetime.fromisoformat(run_summary["session_start"]).astimezone(ZoneInfo("America/New_York"))
    end = datetime.fromisoformat(run_summary["session_end"]).astimezone(ZoneInfo("America/New_York"))
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        requests = connection.execute("SELECT ticker,timeframe FROM strategy_frame_streams ORDER BY ticker,timeframe").fetchall()
        row = connection.execute("SELECT authority_json FROM strategy_frame_streams LIMIT 1").fetchone()
    if not row:
        return False
    authority = json.loads(row[0])
    ends = [end]
    if authority.get("end"):
        ends.append(datetime.fromisoformat(authority["end"]).astimezone(ZoneInfo("America/New_York")))
    leaf = authority.get("chunks", [authority])[0]
    columns = [None]
    if "indicator_columns" in leaf:
        columns.append(tuple(sorted(leaf["indicator_columns"])))
    for candidate_end in ends:
        for projection in columns:
            identity = frame_identity(schema_version=schema_version, start=start, end=candidate_end,
                                      requests=requests, indicator_columns=projection, source_revision=revision)
            if path.name == identity_name(identity):
                register_identity(path, identity, warmup_days=warmup_days)
                return True
    return False


def compatible_artifacts(root: Path, target: dict, *, warmup_days: int):
    """Read small identities only; never scan/cache all historical frame bodies."""
    requested = {tuple(row) for row in target["requests"]}
    for sidecar in sorted(root.glob("*.identity.json")):
        # Sidecars are generated locally and hash-bound to their immutable file.
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        identity = document["identity"]
        path = sidecar.with_name(sidecar.name.removesuffix(".identity.json") + ".sqlite3")
        if not path.is_file() or path.name != identity_name(identity):
            continue
        if (identity["schema_version"] != target["schema_version"]
                or document["warmup_days"] != warmup_days
                or identity["indicator_columns"] != target["indicator_columns"]
                or datetime.fromisoformat(identity["start"]) != datetime.fromisoformat(target["start"])
                or datetime.fromisoformat(identity["end"]) < datetime.fromisoformat(target["end"])):
            continue
        streams = requested.intersection(tuple(row) for row in identity["requests"])
        if streams:
            yield path, identity, streams


def revalidate(identity, *, warmup_days, revision_fetch):
    revision = revision_fetch(
        start=(datetime.fromisoformat(identity["start"]) - timedelta(days=warmup_days)).isoformat(),
        end=identity["end"],
        tickers=tuple(sorted({row[0] for row in identity["requests"]})),
    )
    return (bool(revision.get("complete_for_history")) and bool(revision.get("request_complete"))
            and all(str(revision.get(key) or "") == identity["source_revision"][key] for key in REVISION_FIELDS))


def copy_completed_stream(target: Path, source: Path, ticker: str, timeframe: str, *, end: datetime):
    """One atomic stream copy; SQLite keeps bodies on disk, with bounded page cache.

    The caller has certified identical initialization and covering source scope.
    The original authority stays attached to the copied prefix for audit/V7 use.
    A completion marker is copied only in the same transaction as all its rows.
    """
    connection = sqlite3.connect(target.resolve().as_uri(), uri=True)
    try:
        connection.execute("PRAGMA cache_size=-8192")
        connection.execute("ATTACH DATABASE ? AS donor", (source.as_uri() + "?mode=ro",))
        row = connection.execute(
            "SELECT completed_at, authority_json FROM donor.strategy_frame_streams WHERE ticker=? AND timeframe=?",
            (ticker, timeframe),
        ).fetchone()
        if row is None:
            return None
        with connection:
            connection.execute("DELETE FROM strategy_frames WHERE ticker=? AND timeframe=?", (ticker, timeframe))
            connection.execute(
                "INSERT INTO strategy_frames SELECT * FROM donor.strategy_frames "
                "WHERE ticker=? AND timeframe=? AND as_of_us<=?",
                (ticker, timeframe, int(end.timestamp() * 1_000_000)),
            )
            connection.execute(
                "INSERT OR REPLACE INTO strategy_frame_streams(ticker,timeframe,completed_at,authority_json) VALUES(?,?,?,?)",
                (ticker, timeframe, *row),
            )
        return json.loads(row[1])
    finally:
        connection.close()
