"""Producer-only, restart-safe publication of eligible 100 ms trade prices.

The authoritative market-day certificate and rules are verified by the caller.
No Backtest import may call this module. Incomplete attempts remain invisible
because only a verified coverage-last row authorizes a read.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from pipelines.market_sip.events import liquidity_execution_price_sql as sql
from pipelines.market_sip.events.market_day_sql import literal


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    if not query.rstrip().endswith("FORMAT JSONEachRow"):
        query += " FORMAT JSONEachRow"
    return [json.loads(line) for line in client.execute(query).splitlines()
            if line.strip()]


def _scope(build_id: str, day: date, ticker: str, source_attempt_id: str) -> str:
    UUID(source_attempt_id)
    if not build_id or not ticker or not isinstance(day, date):
        raise ValueError("Eligible-price producer needs a pinned market-day scope")
    return (f"source_build_id={literal(build_id)} "
            f"AND session_date=toDate({literal(day.isoformat())}) "
            f"AND ticker={literal(ticker)} "
            f"AND source_attempt_id=toUUID({literal(source_attempt_id)})")


def _summary(client: Any, scope: str, attempt: str) -> dict[str, Any]:
    rows = _rows(client, f"""SELECT count() AS row_count,
      uniqExact((bucket_index,price_int)) AS unique_keys,
      uniqExact(bucket_index) AS eligible_bucket_count,
      sum(execution_volume) AS total_execution_volume,
      toString(sum(cityHash64(tuple(*)))) AS row_hash
      FROM {sql.TABLE} WHERE {scope}
      AND derivation_attempt_id=toUUID({literal(attempt)})""")
    if len(rows) != 1:
        raise RuntimeError("Eligible-price summary returned an ambiguous scope")
    row = rows[0]
    count = int(row["row_count"])
    if count != int(row["unique_keys"]):
        raise RuntimeError("Eligible-price rows contain duplicate bucket/price keys")
    return row


def _digest(row: dict[str, Any]) -> str:
    values = [str(row[key]) for key in (
        "row_count", "unique_keys", "eligible_bucket_count",
        "total_execution_volume", "row_hash")]
    return sha256("\n".join(values).encode()).hexdigest()


def _certified(client: Any, scope: str) -> list[dict[str, Any]]:
    return _rows(client, f"""SELECT toString(derivation_attempt_id) AS attempt_id,
      price_row_count,eligible_bucket_count,total_execution_volume,content_hash
      FROM {sql.COVERAGE_TABLE} WHERE {scope}""")


def publish_unit(client: Any, *, build_id: str, day: date, ticker: str,
                 source_attempt_id: str, rules: list[dict]) -> str:
    """Publish or verify exactly one source-pinned ticker/day child product.

    If an INSERT outcome is uncertain, the caller stops. A retry allocates a
    fresh derivation attempt; orphan rows never have coverage and cannot be
    consumed. No DELETE, mutation, or replacement is needed.
    """
    scope = _scope(build_id, day, ticker, source_attempt_id)
    existing = _certified(client, scope)
    if len(existing) > 1:
        raise RuntimeError("Eligible-price scope has multiple published attempts")
    if existing:
        certificate = existing[0]
        attempt = certificate["attempt_id"]
        mismatch = _rows(client, sql.bucket_parity_sql(
            build_id, day, ticker, source_attempt_id, attempt))
        result = _summary(client, scope, attempt)
        differences = []
        if mismatch:
            differences.append("bucket_parity")
        if int(certificate["price_row_count"]) != int(result["row_count"]):
            differences.append("row_count")
        if int(certificate["eligible_bucket_count"]) != int(result["eligible_bucket_count"]):
            differences.append("eligible_bucket_count")
        if abs(float(certificate["total_execution_volume"])
               - float(result["total_execution_volume"])) > 1e-6:
            differences.append("total_execution_volume")
        if certificate["content_hash"] != _digest(result):
            differences.append("content_hash")
        if differences:
            raise RuntimeError("Published eligible-price child differs from its coverage: "
                               + ", ".join(differences))
        return "skipped"
    attempt = str(uuid4())
    client.execute(sql.insert_sql(
        build_id, day, ticker, source_attempt_id, attempt, rules))
    mismatch = _rows(client, sql.bucket_parity_sql(
        build_id, day, ticker, source_attempt_id, attempt))
    if mismatch:
        raise RuntimeError(f"Eligible-price bucket parity failed for {day} {ticker}: "
                           f"{len(mismatch)} sampled mismatches")
    result = _summary(client, scope, attempt)
    digest = _digest(result)
    client.execute(f"""INSERT INTO {sql.COVERAGE_TABLE} SELECT
      {literal(build_id)},toDate({literal(day.isoformat())}),{literal(ticker)},
      toUUID({literal(source_attempt_id)}),toUUID({literal(attempt)}),
      toUInt64({int(result['row_count'])}),
      toUInt32({int(result['eligible_bucket_count'])}),
      toFloat64({float(result['total_execution_volume']):.17g}),
      {literal(digest)},now64(6,'UTC')""")
    certificates = _certified(client, scope)
    if (len(certificates) != 1 or certificates[0]["attempt_id"] != attempt
            or int(certificates[0]["price_row_count"]) != int(result["row_count"])
            or int(certificates[0]["eligible_bucket_count"])
            != int(result["eligible_bucket_count"])
            or abs(float(certificates[0]["total_execution_volume"])
                   - float(result["total_execution_volume"])) > 1e-6
            or certificates[0]["content_hash"] != digest):
        raise RuntimeError("Eligible-price coverage publication was not exact")
    return "published"
