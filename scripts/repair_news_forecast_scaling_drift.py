from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "services" / "text-intelligence"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    insert_json_each_row,
    sql_string,
)
from research.text_intelligence.news_synthesis_v1.deepfm_serving import DeepFMServingRelease
from text_intelligence.config import IntelligenceConfig
from text_intelligence.forecast_review import (
    FUNNEL_CONTRACT,
    FUNNEL_TABLE,
    ForecastReviewRuntime,
    _cap_bucket,
)
from src.backend.ticker_facts_service import (
    clickhouse_rows,
    normalize_ticker,
    parse_as_of,
)


BROKEN_RELEASE_ID = "news-deepfm-pre-august-v1"
REPAIRED_RELEASE_ID = "news-deepfm-pre-august-v1-scaling-repair-v2"
REPAIRED_FUNNEL_CONTRACT = "news_forecast_funnel_deepfm_only_serving_v3"
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1"
) / "scaling_repair_v2"

_RUNTIME: ForecastReviewRuntime | None = None
_CLIENT: ClickHouseHttpClient | None = None
NEW_YORK = ZoneInfo("America/New_York")


def _client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), timeout_seconds=60,
    )


def _initialize_worker() -> None:
    global _CLIENT, _RUNTIME
    config = IntelligenceConfig.from_env()
    if config.forecast_release_manifest.name != "release_v2.json":
        raise ValueError("repair requires the versioned DeepFM v2 release manifest")
    _CLIENT = _client()
    _RUNTIME = ForecastReviewRuntime(config, _CLIENT, "q_live")
    _RUNTIME.release = DeepFMServingRelease(
        config.forecast_release_manifest, device=config.forecast_model_device,
    )
    if _RUNTIME.release.release_id != REPAIRED_RELEASE_ID:
        raise ValueError("repair worker loaded an unexpected DeepFM release")


def _repair_one(old: dict[str, Any]) -> dict[str, Any]:
    if _RUNTIME is None or _CLIENT is None or _RUNTIME.release is None:
        raise RuntimeError("repair worker is not initialized")
    source = dict(old.pop("source"))
    scored = _RUNTIME.release.score(
        source,
        ticker_history=dict(old.pop("ticker_history")),
        market_cap=_market_cap_context(source),
        threshold=_RUNTIME.config.forecast_eligibility_threshold,
    )
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    new = {
        "canonical_news_id": str(source["source_id"]),
        "published_at_utc": datetime.fromisoformat(
            str(source["source_timestamp"]).replace("Z", "+00:00")
        ).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
        "rendered_text_hash": str(source.get("rendered_text_hash") or ""),
        "contract_version": FUNNEL_CONTRACT,
        "deterministic_engine_version": "",
        "stage": "deepfm_eligible" if scored["forecast_eligibility"] == "eligible" else "deepfm_filtered",
        "forecast_eligibility": scored["forecast_eligibility"],
        "eligible_probability": scored["eligible_probability"],
        "threshold": scored["threshold"],
        "model_release_id": scored["release_id"],
        "model_release_hash": scored["release_hash"],
        "created_at_utc": now,
    }
    insert_json_each_row(_CLIENT, "q_live", FUNNEL_TABLE, list(new), [new])
    return {
        "canonical_news_id": str(old["canonical_news_id"]),
        "published_at_utc": str(old["published_at_utc"]),
        "rendered_text_hash": str(new["rendered_text_hash"]),
        "old_contract_version": str(old["contract_version"]),
        "old_release_id": str(old["model_release_id"]),
        "old_release_hash": str(old["model_release_hash"]),
        "old_probability": float(old["eligible_probability"]),
        "old_eligibility": str(old["forecast_eligibility"]),
        "new_contract_version": str(new["contract_version"]),
        "new_release_id": str(new["model_release_id"]),
        "new_release_hash": str(new["model_release_hash"]),
        "new_probability": float(new["eligible_probability"]),
        "new_eligibility": str(new["forecast_eligibility"]),
        "corrected_at_utc": str(new["created_at_utc"]),
        "correction_status": "rescore_complete",
    }


def _market_cap_context(source: dict[str, Any]) -> dict[str, Any]:
    if _CLIENT is None:
        raise RuntimeError("repair worker client is not initialized")
    cutoff = parse_as_of(str(source["source_timestamp"]))
    values: list[dict[str, Any]] = []
    for value in sorted({str(item).strip().upper() for item in source.get("tickers") or () if str(item).strip()}):
        try:
            ticker = normalize_ticker(value)
        except ValueError:
            values.append({
                "ticker": value,
                "market_cap": None,
                "market_cap_bucket": "missing",
                "market_cap_source": "invalid_ticker",
            })
            continue
        cutoff_sql = sql_string(cutoff.isoformat())
        cutoff_date_sql = sql_string(cutoff.date().isoformat())
        anchors = clickhouse_rows(_CLIENT, f"""
SELECT symbol_id
FROM q_live.feature_tradable_universe_v1 FINAL
WHERE universe_date<=toDate({cutoff_date_sql})
  AND inserted_at<=parseDateTime64BestEffort({cutoff_sql})
  AND upper(ticker)={sql_string(ticker)}
ORDER BY universe_date DESC,is_tradable DESC,currency_code='USD' DESC,
         product_type='STK' DESC,exchange_code ASC
LIMIT 1 FORMAT JSONEachRow
""")
        market_cap = None
        if anchors:
            symbol_id = str(anchors[0]["symbol_id"])
            market_rows = clickhouse_rows(_CLIENT, f"""
SELECT * FROM
(
 SELECT * FROM q_live.market_security_market_snapshot_v1 FINAL
 WHERE symbol_id={sql_string(symbol_id)}
   AND observed_at_utc<=parseDateTime64BestEffort({cutoff_sql})
   AND inserted_at<=parseDateTime64BestEffort({cutoff_sql})
 ORDER BY observed_at_utc DESC,inserted_at DESC
 LIMIT 1 BY observed_at_utc
)
ORDER BY observed_at_utc DESC,inserted_at DESC
LIMIT 20 FORMAT JSONEachRow
""")
            if market_rows:
                market_cap = market_rows[0].get("market_cap")
        values.append({
            "ticker": ticker,
            "market_cap": market_cap,
            "market_cap_bucket": _cap_bucket(market_cap),
            "market_cap_source": "ticker_facts_asof" if market_cap else "missing",
        })
    known = [float(row["market_cap"]) for row in values if row.get("market_cap")]
    buckets = sorted({str(row["market_cap_bucket"]) for row in values})
    return {
        "market_cap_coverage": "complete" if values and len(known) == len(values) else "partial" if known else "missing",
        "market_cap_known_ticker_count": len(known),
        "market_cap_missing_fraction": 1.0 - len(known) / max(1, len(values)),
        "market_cap_min": min(known) if known else None,
        "market_cap_median": sorted(known)[len(known) // 2] if known else None,
        "market_cap_max": max(known) if known else None,
        "market_cap_min_bucket": _cap_bucket(min(known)) if known else "missing",
        "market_cap_max_bucket": _cap_bucket(max(known)) if known else "missing",
        "market_cap_bucket_set": "|".join(buckets) if buckets else "missing",
        "market_cap_source_set": "ticker_facts_asof" if known else "missing",
        "market_cap_max_age_bucket": "unknown",
        "market_cap_tickers": values,
    }
def _affected_rows(client: ClickHouseHttpClient) -> list[dict[str, Any]]:
    return list(client.iter_json_each_row(f"""
SELECT canonical_news_id,
       toString(argMax(published_at_utc,created_at_utc)) published_at_utc,
       argMax(contract_version,created_at_utc) contract_version,
       argMax(model_release_id,created_at_utc) model_release_id,
       argMax(model_release_hash,created_at_utc) model_release_hash,
       argMax(rendered_text_hash,created_at_utc) rendered_text_hash,
       argMax(eligible_probability,created_at_utc) eligible_probability,
       argMax(forecast_eligibility,created_at_utc) forecast_eligibility
FROM q_live.news_forecast_funnel_v1 AS f FINAL
WHERE f.model_release_id='{BROKEN_RELEASE_ID}'
GROUP BY canonical_news_id
ORDER BY published_at_utc,canonical_news_id
FORMAT JSONEachRow
"""))


def _sources(client: ClickHouseHttpClient) -> dict[str, dict[str, Any]]:
    rows = client.iter_json_each_row(f"""
WITH affected AS
(
 SELECT canonical_news_id,argMax(published_at_utc,created_at_utc) published_at_utc
 FROM q_live.news_forecast_funnel_v1 AS f FINAL
 WHERE f.model_release_id='{BROKEN_RELEASE_ID}'
 GROUP BY canonical_news_id
)
SELECT e.canonical_news_id source_id,concat(toString(e.published_at_utc),'Z') source_timestamp,
       e.provider,e.title,if(empty(r.rendered_text),e.title,r.rendered_text) text,e.tickers,
       e.channels,e.provider_tags,e.content_quality_flags,
       if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
FROM q_live.benzinga_news_event_v2 AS e FINAL
INNER JOIN affected AS a ON a.canonical_news_id=e.canonical_news_id
LEFT JOIN q_live.benzinga_news_rendered_v2 AS r FINAL
 ON r.published_date=e.published_date AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
FORMAT JSONEachRow
""")
    return {str(row["source_id"]): row for row in rows}


def _parse_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _ticker_histories(
    client: ClickHouseHttpClient,
    sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    affected_ids = set(sources)
    timestamps = [_parse_utc(row["source_timestamp"]) for row in sources.values()]
    start_local = min(timestamps).astimezone(NEW_YORK).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    start = start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    end = max(timestamps).strftime("%Y-%m-%d %H:%M:%S.%f")
    events = list(client.iter_json_each_row(f"""
SELECT canonical_news_id,concat(toString(published_at_utc),'Z') source_timestamp,tickers
FROM q_live.benzinga_news_event_v2 FINAL
WHERE published_at_utc>=toDateTime64('{start}',6,'UTC')
  AND published_at_utc<=toDateTime64('{end}',6,'UTC')
ORDER BY published_at_utc,canonical_news_id
FORMAT JSONEachRow
"""))
    counts: dict[tuple[str, str], int] = defaultdict(int)
    previous: dict[tuple[str, str], datetime] = {}
    histories: dict[str, dict[str, Any]] = {}
    position = 0
    while position < len(events):
        published = _parse_utc(events[position]["source_timestamp"])
        stop = position
        while stop < len(events) and _parse_utc(events[stop]["source_timestamp"]) == published:
            stop += 1
        session = published.astimezone(NEW_YORK).date().isoformat()
        for event in events[position:stop]:
            source_id = str(event["canonical_news_id"])
            if source_id not in affected_ids:
                continue
            tickers = sorted({str(value).strip().upper() for value in event.get("tickers") or () if str(value).strip()})
            if not tickers:
                histories[source_id] = {}
                continue
            ordinals = [counts[(session, ticker)] + 1 for ticker in tickers]
            # ClickHouse LEFT JOIN uses the DateTime epoch for a missing prior
            # row under the production join-null setting; reproduce that
            # serving feature exactly instead of "correcting" it during repair.
            recencies = [
                (
                    published - previous.get(
                        (session, ticker), datetime(1970, 1, 1, tzinfo=UTC),
                    )
                ).total_seconds()
                for ticker in tickers
            ]
            minimum = min(recencies)
            histories[source_id] = {
                "min_ticker_session_ordinal": min(ordinals),
                "max_ticker_session_ordinal": max(ordinals),
                "min_seconds_since_previous_ticker_news": minimum,
                "max_seconds_since_previous_ticker_news": max(recencies),
                "any_ticker_first_session": any(value == 1 for value in ordinals),
                "all_tickers_first_session": all(value == 1 for value in ordinals),
                "any_ticker_news_within_5m": minimum is not None and minimum < 300,
                "any_ticker_news_within_30m": minimum is not None and minimum < 1800,
            }
        for event in events[position:stop]:
            for ticker in {str(value).strip().upper() for value in event.get("tickers") or () if str(value).strip()}:
                counts[(session, ticker)] += 1
                previous[(session, ticker)] = published
        position = stop
    if set(histories) != affected_ids:
        raise ValueError("ticker-history reconstruction did not cover every affected source")
    return histories


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def _fail_closed_unavailable(
    client: ClickHouseHttpClient,
    old: dict[str, Any],
    config: IntelligenceConfig,
    release: DeepFMServingRelease,
) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    new = {
        "canonical_news_id": str(old["canonical_news_id"]),
        "published_at_utc": str(old["published_at_utc"]),
        "rendered_text_hash": str(old["rendered_text_hash"]),
        "contract_version": FUNNEL_CONTRACT,
        "deterministic_engine_version": "",
        "stage": "deepfm_filtered",
        "forecast_eligibility": "ineligible",
        "eligible_probability": 0.0,
        "threshold": config.forecast_eligibility_threshold,
        "model_release_id": release.release_id,
        "model_release_hash": release.release_hash,
        "created_at_utc": now,
    }
    insert_json_each_row(client, "q_live", FUNNEL_TABLE, list(new), [new])
    return {
        "canonical_news_id": str(old["canonical_news_id"]),
        "published_at_utc": str(old["published_at_utc"]),
        "rendered_text_hash": str(old["rendered_text_hash"]),
        "old_contract_version": str(old["contract_version"]),
        "old_release_id": str(old["model_release_id"]),
        "old_release_hash": str(old["model_release_hash"]),
        "old_probability": float(old["eligible_probability"]),
        "old_eligibility": str(old["forecast_eligibility"]),
        "new_contract_version": FUNNEL_CONTRACT,
        "new_release_id": release.release_id,
        "new_release_hash": release.release_hash,
        "new_probability": 0.0,
        "new_eligibility": "ineligible",
        "corrected_at_utc": now,
        "correction_status": "source_unavailable_fail_closed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rescore every row produced by the inverted DeepFM serving transform",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--finalize-existing", action="store_true")
    args = parser.parse_args()
    workers = max(1, min(16, int(args.workers)))
    if args.finalize_existing:
        ledger_path = args.output_root / "CORRECTION_LEDGER.jsonl"
        report_path = args.output_root / "REPORT.json"
        rows = [
            json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in rows:
            row.setdefault("correction_status", "rescore_complete")
        temporary = ledger_path.with_suffix(".normalized.jsonl")
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(temporary, ledger_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["rescored_rows"] = sum(
            row["correction_status"] == "rescore_complete" for row in rows
        )
        report["source_unavailable_fail_closed"] = sum(
            row["correction_status"] == "source_unavailable_fail_closed" for row in rows
        )
        _write_json(report_path, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    config = IntelligenceConfig.from_env()
    client = _client()
    try:
        affected = _affected_rows(client)
        sources = _sources(client)
        histories = _ticker_histories(client, sources)
    except Exception:
        client.close()
        raise
    if not affected:
        raise RuntimeError("no rows from the broken release were found")
    args.output_root.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output_root / "CORRECTION_LEDGER.jsonl"
    report_path = args.output_root / "REPORT.json"
    if report_path.exists():
        raise FileExistsError("completed scaling repair report already exists")
    corrected: list[dict[str, Any]] = []
    if ledger_path.exists():
        corrected = [
            json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in corrected:
            row.setdefault("correction_status", "rescore_complete")
    completed_ids = {str(row["canonical_news_id"]) for row in corrected}
    if len(completed_ids) != len(corrected):
        raise ValueError("correction ledger contains duplicate canonical News IDs")
    unavailable = [
        row for row in affected
        if str(row["canonical_news_id"]) not in completed_ids
        and str(row["canonical_news_id"]) not in sources
    ]
    if unavailable:
        release = DeepFMServingRelease(config.forecast_release_manifest, device="cpu")
        with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            for old in unavailable:
                row = _fail_closed_unavailable(client, old, config, release)
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                corrected.append(row)
                completed_ids.add(str(row["canonical_news_id"]))
    client.close()
    remaining = [
        row for row in affected
        if str(row["canonical_news_id"]) not in completed_ids
    ]
    for row in remaining:
        source_id = str(row["canonical_news_id"])
        row["source"] = sources[source_id]
        row["ticker_history"] = histories[source_id]

    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, initializer=_initialize_worker) as pool:
        with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            for index, row in enumerate(
                pool.imap_unordered(_repair_one, remaining, chunksize=1),
                start=len(corrected) + 1,
            ):
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                corrected.append(row)
                if index % 100 == 0 or index == len(affected):
                    print(json.dumps({"corrected": index, "total": len(affected)}), flush=True)

    changed = [row for row in corrected if row["old_eligibility"] != row["new_eligibility"]]
    report = {
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "broken_release_id": BROKEN_RELEASE_ID,
        "repaired_release_id": REPAIRED_RELEASE_ID,
        "repaired_funnel_contract": REPAIRED_FUNNEL_CONTRACT,
        "affected_rows": len(affected),
        "corrected_rows": len(corrected),
        "rescored_rows": sum(
            row["correction_status"] == "rescore_complete" for row in corrected
        ),
        "source_unavailable_fail_closed": sum(
            row.get("correction_status") == "source_unavailable_fail_closed"
            for row in corrected
        ),
        "eligibility_changed": len(changed),
        "eligible_to_ineligible": sum(
            row["old_eligibility"] == "eligible" and row["new_eligibility"] == "ineligible"
            for row in corrected
        ),
        "ineligible_to_eligible": sum(
            row["old_eligibility"] == "ineligible" and row["new_eligibility"] == "eligible"
            for row in corrected
        ),
        "downstream_reconciliation": {
            "current_projection": "latest funnel row now resolves to the repaired v3 contract",
            "historical_signal_occurrences": "preserved as immutable provenance; canonical_news_id links each occurrence to this correction ledger",
            "silent_rewrite": False,
        },
        "ledger": str(ledger_path),
    }
    _write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
