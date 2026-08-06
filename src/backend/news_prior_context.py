from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string
from research.text_intelligence.news_synthesis_v1.storage import LIVE_SEMANTIC_TABLE


DATABASE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.:\-]{0,23}$")


def prior_news_context(
    client: ClickHouseHttpClient,
    *,
    canonical_news_id: str,
    ticker: str,
    as_of_utc: str,
    limit: int = 3,
    database: str = "q_live",
    include_semantic: bool | None = None,
) -> list[dict[str, Any]]:
    """Return causal same-ticker news plus only already-observable reactions."""

    if not DATABASE_PATTERN.fullmatch(database):
        raise ValueError("news database is not a valid identifier")
    symbol = ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(symbol):
        raise ValueError("ticker is invalid")
    cutoff = parse_timestamp(as_of_utc)
    safe_limit = max(0, min(int(limit), 3))
    if safe_limit == 0:
        return []
    if include_semantic is None:
        include_semantic = table_exists(
            client, database=database, table=LIVE_SEMANTIC_TABLE
        )
    semantic_table = LIVE_SEMANTIC_TABLE
    if include_semantic and not table_exists(
        client, database=database, table=semantic_table
    ):
        include_semantic = False
    cutoff_sql = clickhouse_timestamp(cutoff)
    semantic_column = (
        "ifNull(s.semantic_json, '') AS semantic_json"
        if include_semantic
        else "'' AS semantic_json"
    )
    semantic_join = (
        f"""
            LEFT JOIN
            (
                SELECT canonical_news_id, ticker,
                       argMax(semantic_json, created_at_utc) AS semantic_json
                FROM `{database}`.`{semantic_table}`
                GROUP BY canonical_news_id, ticker
            ) AS s
              ON s.canonical_news_id = t.canonical_news_id
             AND s.ticker = t.ticker
        """
        if include_semantic
        else ""
    )
    rows = json_each_row(
        client.execute(
            f"""
            SELECT t.canonical_news_id AS canonical_news_id,
                   toString(t.published_at_utc) AS published_at_utc,
                   e.title AS title,
                   substring(r.rendered_text, 1, 6000) AS rendered_excerpt,
                   e.channels AS channels,
                   e.provider_tags AS provider_tags,
                   {semantic_column}
            FROM (SELECT * FROM `{database}`.`benzinga_news_ticker_v2` FINAL) AS t
            INNER JOIN (SELECT * FROM `{database}`.`benzinga_news_event_v2` FINAL) AS e
              ON e.canonical_news_id = t.canonical_news_id
            INNER JOIN (SELECT * FROM `{database}`.`benzinga_news_rendered_v2` FINAL) AS r
              ON r.canonical_news_id = t.canonical_news_id
             AND r.rendered_text_hash = t.rendered_text_hash
            {semantic_join}
            WHERE t.ticker = {sql_string(symbol)}
              AND t.published_at_utc < {cutoff_sql}
              AND t.canonical_news_id != {sql_string(canonical_news_id)}
            ORDER BY t.published_at_utc DESC, t.canonical_news_id DESC
            LIMIT {safe_limit}
            FORMAT JSONEachRow
            """
        )
    )
    if not rows:
        return []
    require_columns(
        rows,
        {
            "canonical_news_id",
            "published_at_utc",
            "title",
            "rendered_excerpt",
            "channels",
            "provider_tags",
            "semantic_json",
        },
        stage="prior_news",
    )
    ids = ",".join(sql_string(str(row["canonical_news_id"])) for row in rows)
    reactions = json_each_row(
        client.execute(
            f"""
            SELECT l.canonical_news_id AS canonical_news_id,
                   l.horizon_code AS horizon_code,
                   l.target_return AS target_return,
                   l.high_return AS high_return,
                   l.low_return AS low_return,
                   toString(l.target_at_utc) AS target_at_utc
            FROM (SELECT * FROM `{database}`.`news_reaction_labels_v2` FINAL) AS l
            INNER JOIN
            (
                SELECT *
                FROM `{database}`.`news_reaction_quality_overlay_v1` FINAL
            ) AS q
              ON q.canonical_news_id = l.canonical_news_id
             AND q.ticker = l.ticker
             AND q.published_at_utc = l.published_at_utc
             AND q.horizon_code = l.horizon_code
            WHERE l.canonical_news_id IN ({ids})
              AND l.ticker = {sql_string(symbol)}
              AND l.target_at_utc <= {cutoff_sql}
              AND l.applicable = 1
              AND l.quality_status = 'clean'
              AND q.eligible_for_statistics = 1
              AND isNotNull(l.target_return)
              AND isNotNull(l.high_return)
              AND isNotNull(l.low_return)
              AND isFinite(l.target_return)
              AND isFinite(l.high_return)
              AND isFinite(l.low_return)
            ORDER BY l.canonical_news_id, l.target_at_utc, l.horizon_code
            FORMAT JSONEachRow
            """
        )
    )
    require_columns(
        reactions,
        {
            "canonical_news_id",
            "horizon_code",
            "target_return",
            "high_return",
            "low_return",
            "target_at_utc",
        },
        stage="prior_news_reactions",
    )
    reaction_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for row in reactions:
        reaction_by_id.setdefault(str(row["canonical_news_id"]), {})[
            str(row["horizon_code"])
        ] = {
            "terminal_return_pct": float(row["target_return"]) * 100.0,
            "high_return_pct": float(row["high_return"]) * 100.0,
            "low_return_pct": float(row["low_return"]) * 100.0,
            "observed_through_utc": row["target_at_utc"],
        }
    result: list[dict[str, Any]] = []
    for row in rows:
        semantic_text = str(row.pop("semantic_json") or "")
        row["semantic_label"] = json.loads(semantic_text) if semantic_text else None
        row["completed_reactions"] = reaction_by_id.get(
            str(row["canonical_news_id"]), {}
        )
        result.append(row)
    return result


def table_exists(
    client: ClickHouseHttpClient, *, database: str, table: str
) -> bool:
    rows = json_each_row(
        client.execute(
            f"""
            SELECT count() AS rows
            FROM system.tables
            WHERE database = {sql_string(database)}
              AND name = {sql_string(table)}
            FORMAT JSONEachRow
            """
        )
    )
    return bool(rows and int(rows[0].get("rows") or 0) > 0)


def parse_timestamp(value: str) -> datetime:
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clickhouse_timestamp(value: datetime) -> str:
    text = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(text)}, 6, 'UTC')"


def json_each_row(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def require_columns(
    rows: list[dict[str, Any]], required: set[str], *, stage: str
) -> None:
    if not rows:
        return
    missing = sorted(required - set(rows[0]))
    if missing:
        raise RuntimeError(
            f"{stage} result schema mismatch: missing={missing}, "
            f"returned={sorted(rows[0])}"
        )
