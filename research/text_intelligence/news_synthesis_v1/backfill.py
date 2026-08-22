from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, sql_string
from research.mlops.env import discover_env_files, load_env_files

from .engine import ENGINE_VERSION, NewsSynthesisEngine
from .funnel import NewsSynthesisFunnel
from .provider_market_cap_analysis import cap_bucket, load_provider_snapshot_indexes, parse_utc
from .storage import (
    create_tables,
    load_identity_index,
    persist_documents,
    persist_funnel_results,
    write_status,
)


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_env_files(Path.cwd()))
    parser = argparse.ArgumentParser(description="Resumable News Synthesis V1 historical backfill")
    parser.add_argument("--start", required=True); parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--workers", type=int, default=8); parser.add_argument("--database", default="q_live")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end_exclusive)
    if end <= start: raise ValueError("end-exclusive must be after start")
    client = _client()
    if args.execute:
        create_tables(client, args.database)
    funnel = NewsSynthesisFunnel(NewsSynthesisEngine(load_identity_index(client, args.database)))
    client.close()
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    print(f"NEWS SYNTHESIS V1 | days={len(days):,} workers={args.workers} execute={args.execute}")
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(32, args.workers))) as pool:
        futures = {pool.submit(_build_day, day, args.database, funnel, args.execute): day for day in days}
        for index, future in enumerate(as_completed(futures), 1):
            day = futures[future]
            try: total, completed = future.result(); print(f"[{index:,}/{len(days):,}] {day} rows={total:,} completed={completed:,}", flush=True)
            except Exception as exc: failures += 1; print(f"[{index:,}/{len(days):,}] {day} FAILED {type(exc).__name__}: {exc}", flush=True)
    return 1 if failures else 0


def _build_day(day: date, database: str, funnel: NewsSynthesisFunnel, execute: bool) -> tuple[int, int]:
    client = _client(); day_text = day.isoformat()
    try:
        rows = list(client.iter_json_each_row(f"""SELECT e.canonical_news_id source_id,toString(e.published_at_utc) source_timestamp,e.provider,e.title,e.author,e.article_url,e.url_domain,
if(empty(r.rendered_text),e.title,r.rendered_text) text,e.tickers,e.channels,e.provider_tags,e.content_quality_flags,r.quality_flags,e.source_revision_key,
multiIf(empty(r.canonical_news_id),'unrendered',r.source_count=0,'title_only','rendered') render_status,
if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
FROM `{database}`.`benzinga_news_event_v2` e FINAL
LEFT JOIN `{database}`.`benzinga_news_rendered_v2` r FINAL ON r.published_date=e.published_date AND r.provider_article_id=e.provider_article_id AND r.source_revision_key=e.source_revision_key
PREWHERE e.published_date=toDate({sql_string(day_text)}) FORMAT JSONEachRow"""))
        _attach_causal_provider_market_caps(rows, client=client, database=database, day=day)
        source_revision = _source_revision(rows)
        current = int(client.execute(f"SELECT count() FROM `{database}`.`news_synthesis_build_status_v1` FINAL WHERE published_date=toDate({sql_string(day_text)}) AND engine_version={sql_string(ENGINE_VERSION)} AND source_rows={len(rows)} AND source_revision={sql_string(source_revision)} AND status='complete'").strip() or "0") if execute else 0
        if current: return len(rows), len(rows)
        results = [funnel.process(row) for row in rows]
        documents = [row["synthesis_document"] for row in results if row["synthesis_document"] is not None]
        if execute:
            persist_documents(client, database, documents)
            completed = persist_funnel_results(client, database, results)
        else:
            completed = len(results)
        if execute: write_status(client, database, published_date=day_text, source_rows=len(rows), completed_rows=completed, failed_rows=0, source_revision=source_revision, status="complete")
        return len(rows), completed
    except Exception as exc:
        if execute: write_status(client, database, published_date=day_text, source_rows=0, completed_rows=0, failed_rows=1, source_revision="0" * 64, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally: client.close()


def _client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=60)


def _source_revision(rows: list[dict[str, object]]) -> str:
    """Hash every source field that can change synthesis, not merely row count."""
    fields = (
        "source_id", "source_timestamp", "source_revision_key", "rendered_text_hash", "provider",
        "title", "author", "article_url", "url_domain", "tickers", "channels", "provider_tags", "content_quality_flags",
        "quality_flags", "render_status",
        "market_cap_tickers",
    )
    material = [
        {field: row.get(field) for field in fields}
        for row in sorted(rows, key=lambda item: (str(item.get("source_id") or ""), str(item.get("source_revision_key") or "")))
    ]
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _attach_causal_provider_market_caps(
    rows: list[dict[str, object]],
    *,
    client: ClickHouseHttpClient,
    database: str,
    day: date,
) -> None:
    tickers = {
        str(ticker).strip().upper()
        for row in rows for ticker in row.get("tickers") or () if str(ticker).strip()
    }
    indexes = load_provider_snapshot_indexes(
        client,
        database=database,
        table="market_security_market_snapshot_v1",
        tickers=tickers,
        start=datetime.combine(day, time.min, tzinfo=UTC),
        end=datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC),
    )
    for row in rows:
        published = parse_utc(row["source_timestamp"])
        contexts = []
        for raw_ticker in row.get("tickers") or ():
            ticker = str(raw_ticker).strip().upper()
            index = indexes.get(f"ticker:{ticker}")
            value = index.before(published) if index else None
            contexts.append({
                "ticker": ticker,
                "market_cap": value.value if value else None,
                "market_cap_bucket": cap_bucket(value.value if value else None),
                "market_cap_source": "provider_snapshot_ticker_fallback" if value else "missing",
                "market_cap_available_at_utc": value.available_at.isoformat() if value else None,
            })
        row["market_cap_tickers"] = contexts
