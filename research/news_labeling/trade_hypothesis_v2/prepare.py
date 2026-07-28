from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, time as wall_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from research.news_labeling.gpt_oss_v1.data import read_jsonl
from src.backend.news_prior_context import prior_news_context
from src.backend.sec_canvas_service import sec_filings_payload
from src.backend.ticker_facts_service import ticker_facts_payload

from .contract import CONTRACT_VERSION, PROMPT_VERSION


NEW_YORK = ZoneInfo("America/New_York")
SAMPLE_CANDIDATES = (
    Path(r"D:\TradingML\runtimes\news_labeling\gpt_oss_v1\shared\sample.jsonl"),
    Path(r"D:\TradingML\runtimes\news_labeling\gpt_oss_v1\sample.jsonl"),
    Path(
        r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
        r"\news_labeling\gpt_oss_v1\shared\sample.jsonl"
    ),
    Path(
        r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
        r"\news_labeling\gpt_oss_v1\sample.jsonl"
    ),
)
SOL_LABEL_CANDIDATES = (
    Path(
        r"D:\TradingML\runtimes\news_labeling\openai_batch_v1"
        r"\models\gpt-5.6-sol\labels.jsonl"
    ),
    Path(
        r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
        r"\news_labeling\openai_batch_v1\models\gpt-5.6-sol\labels.jsonl"
    ),
)


def default_runtime_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_TRADE_HYPOTHESIS_V2_ROOT",
            r"D:\TradingML\runtimes\news_labeling\trade_hypothesis_v2",
        )
    )


def ensure_manifest(
    *,
    runtime_root: Path,
    workers: int,
    sample_path: Path | None = None,
    sol_labels_path: Path | None = None,
) -> Path:
    shared = runtime_root / "shared"
    manifest_path = shared / "contexts.jsonl"
    authority_path = shared / "manifest.json"
    if manifest_path.exists() and authority_path.exists():
        validate_manifest(manifest_path, authority_path)
        return manifest_path
    shared.mkdir(parents=True, exist_ok=True)
    lock = shared / "prepare.lock"
    acquired = False
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        acquired = True
    except FileExistsError:
        pass
    if not acquired:
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            if manifest_path.exists() and authority_path.exists():
                validate_manifest(manifest_path, authority_path)
                return manifest_path
            if not lock.exists():
                return ensure_manifest(
                    runtime_root=runtime_root,
                    workers=workers,
                    sample_path=sample_path,
                    sol_labels_path=sol_labels_path,
                )
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for shared context build: {lock}")
    try:
        build_manifest(
            manifest_path=manifest_path,
            authority_path=authority_path,
            workers=workers,
            sample_path=sample_path or first_existing(SAMPLE_CANDIDATES),
            sol_labels_path=sol_labels_path or first_existing(SOL_LABEL_CANDIDATES),
        )
    finally:
        lock.unlink(missing_ok=True)
    return manifest_path


def build_manifest(
    *,
    manifest_path: Path,
    authority_path: Path,
    workers: int,
    sample_path: Path,
    sol_labels_path: Path,
) -> None:
    sample = read_jsonl(sample_path)
    single = [row for row in sample if len(row.get("tickers") or []) == 1]
    if len(sample) != 192 or len(single) != 90:
        raise RuntimeError(
            f"Frozen population drift: total={len(sample)}, single_ticker={len(single)}"
        )
    sol = {
        str(row["canonical_news_id"]): row["label"]
        for row in read_jsonl(sol_labels_path)
        if row.get("status") == "completed" and isinstance(row.get("label"), dict)
    }
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 16))) as pool:
        futures = {
            pool.submit(build_context_row, article, sol.get(str(article["canonical_news_id"]))): article
            for article in single
        }
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows.append(row)
            elapsed = time.monotonic() - started
            rate = index / elapsed if elapsed else 0.0
            eta = (len(single) - index) / rate if rate else 0.0
            print(
                f"CONTEXT {index}/90 id={row['canonical_news_id']} "
                f"market={row['context']['qmd_snapshot'].get('available', False)} "
                f"prior={len(row['context']['prior_news'])} "
                f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
                flush=True,
            )
    rows.sort(key=lambda row: (row["published_at_utc"], row["canonical_news_id"]))
    atomic_jsonl(manifest_path, rows)
    authority = {
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "sample_path": str(sample_path),
        "sample_sha256": sha256_file(sample_path),
        "sol_labels_path": str(sol_labels_path),
        "sol_labels_sha256": sha256_file(sol_labels_path),
        "rows": len(rows),
        "rows_with_sol_semantic_label": sum(
            row["semantic_label_source"] == "sol" for row in rows
        ),
        "rows_with_market_snapshot": sum(
            bool(row["context"]["qmd_snapshot"].get("available")) for row in rows
        ),
        "context_sha256": sha256_file(manifest_path),
        "host": socket.gethostname(),
        "generated_at_utc": utc_now(),
    }
    atomic_json(authority_path, authority)


def build_context_row(
    article: dict[str, Any], sol_semantic_label: dict[str, Any] | None
) -> dict[str, Any]:
    identifier = str(article["canonical_news_id"])
    ticker = str(article["tickers"][0]).upper()
    published = parse_timestamp(str(article["published_at_utc"]))
    client = clickhouse_client()
    try:
        snapshot = historical_market_snapshot(client, ticker, published)
        prior = prior_news_context(
            client,
            canonical_news_id=identifier,
            ticker=ticker,
            as_of_utc=published.isoformat(),
            limit=3,
        )
        targets, publication_session = current_targets(
            client, identifier, ticker, published
        )
    finally:
        client.close()
    market_status = {
        "session": publication_session,
        "local_time_et": published.astimezone(NEW_YORK).isoformat(timespec="seconds"),
        "source": "certified_reaction_authority",
    }
    facts = ticker_facts_payload(ticker, as_of=published.isoformat())
    sec = sec_filings_payload(
        as_of=published.isoformat(),
        ticker=ticker,
        limit=5,
        lookback_hours=24 * 366,
    )
    semantic = sol_semantic_label or {
        "status": "semantic_label_unavailable",
        "deterministic_evidence": article.get("deterministic") or {},
    }
    context = {
        "contract": "frozen_market_context_v2",
        "context_as_of_utc": published.isoformat(),
        "news_as_of_utc": published.isoformat(),
        "ticker": ticker,
        "title": article.get("title", ""),
        "rendered_text": article.get("rendered_text", ""),
        "semantic_label": semantic,
        "qmd_snapshot": snapshot,
        "market_status": json_safe(market_status),
        "sec_context": list(sec.get("rows") or [])[:5],
        "fundamental_context": facts,
        "prior_news": prior,
    }
    return {
        "canonical_news_id": identifier,
        "ticker": ticker,
        "published_at_utc": published.isoformat(),
        "text_sha256": article.get("text_sha256", ""),
        "semantic_label_source": "sol" if sol_semantic_label else "deterministic_fallback",
        "context": context,
        "targets": targets,
    }


def historical_market_snapshot(
    client: ClickHouseHttpClient, ticker: str, published: datetime
) -> dict[str, Any]:
    if published.year < 2019:
        return {"available": False, "reason": "compact_events_begin_2019"}
    local = published.astimezone(NEW_YORK)
    session_date = local.date()
    if local.time() < wall_time(4):
        session_date = session_date.fromordinal(session_date.toordinal() - 1)
    session_start = datetime.combine(session_date, wall_time(4), NEW_YORK).astimezone(UTC)
    pub_us = int(published.timestamp() * 1_000_000)
    start_us = int(session_start.timestamp() * 1_000_000)
    event_dates = sorted({session_date.isoformat(), local.date().isoformat()})
    tables = sorted({f"events_{day[:4]}" for day in event_dates})
    sources = "\nUNION ALL\n".join(
        f"""
        SELECT sip_timestamp_us, ordinal, event_meta, size_primary, size_secondary,
               bitAnd(event_meta, 1) AS event_type,
               toFloat64(price_primary_int) /
                 if(bitAnd(event_meta, 2) > 0, 10000.0, 100.0) AS primary_price,
               toFloat64(price_secondary_int) /
                 if(bitAnd(event_meta, 4) > 0, 10000.0, 100.0) AS secondary_price
        FROM `market_sip_compact`.`{table}` FINAL
        PREWHERE event_date IN ({", ".join(f"toDate({sql_string(day)})" for day in event_dates)})
          AND ticker = {sql_string(ticker)}
        WHERE sip_timestamp_us >= session_start_us AND sip_timestamp_us <= pub_us
        """
        for table in tables
    )
    sql = f"""
    WITH
      toUInt64({pub_us}) AS pub_us,
      toUInt64({start_us}) AS session_start_us
    SELECT
      argMaxIf(primary_price, tuple(sip_timestamp_us, ordinal),
               event_type = 1 AND primary_price > 0) AS last_price,
      argMaxIf(primary_price, tuple(sip_timestamp_us, ordinal),
               event_type = 0 AND primary_price > 0 AND secondary_price > 0
                 AND primary_price >= secondary_price) AS ask,
      argMaxIf(secondary_price, tuple(sip_timestamp_us, ordinal),
               event_type = 0 AND primary_price > 0 AND secondary_price > 0
                 AND primary_price >= secondary_price) AS bid,
      argMaxIf(size_primary, tuple(sip_timestamp_us, ordinal),
               event_type = 0 AND primary_price > 0 AND secondary_price > 0
                 AND primary_price >= secondary_price) AS ask_size,
      argMaxIf(size_secondary, tuple(sip_timestamp_us, ordinal),
               event_type = 0 AND primary_price > 0 AND secondary_price > 0
                 AND primary_price >= secondary_price) AS bid_size,
      sumIf(size_primary, event_type = 1 AND primary_price > 0) AS day_volume,
      sumIf(size_primary * primary_price, event_type = 1 AND primary_price > 0) AS day_dollar_volume,
      countIf(event_type = 1 AND primary_price > 0) AS day_trade_count,
      countIf(event_type = 1 AND primary_price > 0 AND sip_timestamp_us > pub_us - 10000000) / 10.0 AS trade_rate_10s,
      countIf(event_type = 1 AND primary_price > 0 AND sip_timestamp_us > pub_us - 60000000) / 60.0 AS trade_rate_60s,
      max(sip_timestamp_us) AS last_event_us
    FROM
    (
      {sources}
    )
    FORMAT JSONEachRow
    """
    try:
        rows = json_rows(client.execute(sql))
    except Exception as error:
        return {"available": False, "reason": type(error).__name__}
    if not rows or int(rows[0].get("last_event_us") or 0) <= 0:
        return {"available": False, "reason": "no_prior_market_event"}
    row = rows[0]
    last_event_us = int(row.pop("last_event_us"))
    row.update(
        {
            "available": True,
            "ticker": ticker,
            "last_event_ts": datetime.fromtimestamp(
                last_event_us / 1_000_000, UTC
            ).isoformat(),
            "spread": max(0.0, float(row.get("ask") or 0) - float(row.get("bid") or 0)),
            "source": "market_sip_compact.events",
            "strictly_as_of_utc": published.isoformat(),
        }
    )
    return row


def current_targets(
    client: ClickHouseHttpClient,
    canonical_news_id: str,
    ticker: str,
    published: datetime,
) -> tuple[dict[str, dict[str, float]], str]:
    rows = json_rows(
        client.execute(
            f"""
            SELECT l.horizon_code, l.publication_session,
                   l.target_return, l.high_return, l.low_return
            FROM (SELECT * FROM q_live.news_reaction_labels_v2 FINAL) AS l
            INNER JOIN
              (SELECT * FROM q_live.news_reaction_quality_overlay_v1 FINAL) AS q
              ON q.canonical_news_id=l.canonical_news_id
             AND q.ticker=l.ticker
             AND q.published_at_utc=l.published_at_utc
             AND q.horizon_code=l.horizon_code
            WHERE l.canonical_news_id={sql_string(canonical_news_id)}
              AND l.ticker={sql_string(ticker)}
              AND l.published_at_utc={clickhouse_timestamp(published)}
              AND l.applicable=1 AND l.quality_status='clean'
              AND q.eligible_for_statistics=1
              AND isNotNull(l.target_return)
              AND isNotNull(l.high_return)
              AND isNotNull(l.low_return)
            FORMAT JSONEachRow
            """
        )
    )
    targets = {
        str(row["horizon_code"]): {
            "terminal_return_pct": float(row["target_return"]) * 100.0,
            "high_return_pct": float(row["high_return"]) * 100.0,
            "low_return_pct": float(row["low_return"]) * 100.0,
        }
        for row in rows
    }
    session = str(rows[0].get("publication_session") or "unknown") if rows else "unknown"
    return targets, session


def validate_manifest(path: Path, authority_path: Path) -> None:
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    if authority.get("contract_version") != CONTRACT_VERSION:
        raise RuntimeError("Prepared hypothesis context contract drift")
    if int(authority.get("rows") or 0) != 90:
        raise RuntimeError("Prepared hypothesis context must contain exactly 90 rows")
    if authority.get("context_sha256") != sha256_file(path):
        raise RuntimeError("Prepared hypothesis context hash mismatch")


def first_existing(paths: tuple[Path, ...]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError("None of the required frozen runtime inputs exists")


def clickhouse_client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=180,
    )


def clickhouse_timestamp(value: datetime) -> str:
    text = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(text)}, 6, 'UTC')"


def parse_timestamp(value: str) -> datetime:
    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def json_rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
