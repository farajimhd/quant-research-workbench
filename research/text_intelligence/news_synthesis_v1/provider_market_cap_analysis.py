from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator, Mapping, Sequence

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files

from .provider_filter_analysis import (
    Counts,
    SPLITS,
    candidate_grade,
    canonical_json,
    feature_names,
    feature_report_rows,
    sha256_path,
    write_csv_new,
    write_json_new,
)


ANALYSIS_VERSION = "news_synthesis_provider_market_cap_analysis_v3"
SHARE_TAGS = ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v6_provider_path_exceptions_final\ARTICLE_FEATURES.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_market_cap_context_analysis_v3"
)
DEFAULT_DATABASE = "q_live"
DEFAULT_MACRO_DATABASE = "market_sip_compact"
DEFAULT_MACRO_TABLE = "macro_bars_by_time_symbol"
DEFAULT_BRIDGE_TABLE = "id_sec_market_bridge_v3"
DEFAULT_SEC_TABLE = "sec_xbrl_company_fact_v3"
DEFAULT_SNAPSHOT_TABLE = "market_security_market_snapshot_v1"


@dataclass(frozen=True, slots=True)
class TimedValue:
    available_at: datetime
    value: float
    observed_at: datetime
    source: str


class TimeIndex:
    """Strictly-prior lookup over deterministic point-in-time observations."""

    def __init__(self, values: Iterable[TimedValue]) -> None:
        ordered = sorted(values, key=lambda item: (item.available_at, item.observed_at, item.source))
        self.values = ordered
        self.times = [item.available_at for item in ordered]

    def before(self, timestamp: datetime) -> TimedValue | None:
        position = bisect.bisect_left(self.times, timestamp) - 1
        return self.values[position] if position >= 0 else None


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}: {exc}") from exc


def parse_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cap_bucket(value: float | None) -> str:
    if value is None or not math.isfinite(value) or value <= 0:
        return "missing"
    if value < 50_000_000:
        return "nano_lt_50m"
    if value < 300_000_000:
        return "micro_50m_300m"
    if value < 2_000_000_000:
        return "small_300m_2b"
    if value < 10_000_000_000:
        return "mid_2b_10b"
    if value < 200_000_000_000:
        return "large_10b_200b"
    return "mega_gte_200b"


def chars_bucket(value: int) -> str:
    if value <= 200:
        return "lte_200"
    if value <= 500:
        return "201_500"
    if value <= 1_000:
        return "501_1000"
    if value <= 2_500:
        return "1001_2500"
    if value <= 5_000:
        return "2501_5000"
    return "gt_5000"


def age_bucket(days: float | None) -> str:
    if days is None or not math.isfinite(days) or days < 0:
        return "missing"
    if days <= 1:
        return "lte_1d"
    if days <= 7:
        return "2_7d"
    if days <= 30:
        return "8_30d"
    if days <= 90:
        return "31_90d"
    if days <= 365:
        return "91_365d"
    return "gt_365d"


def _in(values: Iterable[Any]) -> str:
    unique = sorted({str(value).strip() for value in values if str(value).strip()})
    return ",".join(sql_string(value) for value in unique) or "''"


def _json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def load_articles(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in iter_jsonl(path):
        source_id = str(row["source_id"])
        if source_id in seen:
            raise ValueError(f"duplicate article feature source_id: {source_id}")
        seen.add(source_id)
        if str(row.get("label")) not in {"eligible", "ineligible"}:
            continue
        published = parse_utc(row["published_at_text"])
        row["published_at_utc"] = published
        rows.append(row)
        by_month[str(row["published_month"])].append(row)
    if {str(row["split"]) for row in rows} != set(SPLITS):
        raise ValueError("article features do not contain the exact analysis splits")
    rows.sort(key=lambda row: (row["published_at_utc"], row["source_id"]))
    return rows, dict(sorted(by_month.items()))


def load_bridge_index(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    tickers: Iterable[str],
    start: date,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    db = quote_ident(database)
    rows = _json_rows(client, f"""
SELECT upper(ticker) AS ticker, cik, symbol_id, valid_from_date, valid_to_date_exclusive, confidence_score
FROM {db}.{quote_ident(table)} FINAL
WHERE upper(ticker) IN ({_in(tickers)}) AND cik != ''
  AND (valid_from_date IS NULL OR valid_from_date < toDate({sql_string(end.isoformat())}))
  AND (valid_to_date_exclusive IS NULL OR valid_to_date_exclusive > toDate({sql_string(start.isoformat())}))
ORDER BY ticker, confidence_score DESC
FORMAT JSONEachRow
""")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ticker"]).upper()].append(row)
    return dict(grouped)


def resolve_bridge(index: Mapping[str, Sequence[Mapping[str, Any]]], ticker: str, day: date) -> Mapping[str, Any] | None:
    candidates: list[Mapping[str, Any]] = []
    for row in index.get(ticker.upper(), ()):
        valid_from = date.fromisoformat(str(row["valid_from_date"])[:10]) if row.get("valid_from_date") else date.min
        valid_to = date.fromisoformat(str(row["valid_to_date_exclusive"])[:10]) if row.get("valid_to_date_exclusive") else date.max
        if valid_from <= day < valid_to:
            candidates.append(row)
    return max(candidates, key=lambda row: float(row.get("confidence_score") or 0), default=None)


def load_share_indexes(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    ciks: Iterable[str],
    start: datetime,
    end: datetime,
) -> dict[str, TimeIndex]:
    db = quote_ident(database)
    tags = _in(SHARE_TAGS)
    rows = _json_rows(client, f"""
SELECT cik, tag, value, available_at FROM (
  SELECT cik, tag,
    argMax(value, tuple(filed_at_utc, period_end_date, inserted_at)) AS value,
    max(filed_at_utc) AS available_at
  FROM {db}.{quote_ident(table)} FINAL
  WHERE cik IN ({_in(ciks)}) AND taxonomy='us-gaap' AND tag IN ({tags})
    AND filed_at_utc < parseDateTime64BestEffort({sql_string(start.isoformat())})
  GROUP BY cik, tag
  UNION ALL
  SELECT cik, tag,
    argMax(value, tuple(period_end_date, inserted_at)) AS value,
    filed_at_utc AS available_at
  FROM {db}.{quote_ident(table)} FINAL
  WHERE cik IN ({_in(ciks)}) AND taxonomy='us-gaap' AND tag IN ({tags})
    AND filed_at_utc >= parseDateTime64BestEffort({sql_string(start.isoformat())})
    AND filed_at_utc < parseDateTime64BestEffort({sql_string(end.isoformat())})
  GROUP BY cik, tag, filed_at_utc
)
WHERE available_at IS NOT NULL AND value > 0
FORMAT JSONEachRow
""")
    grouped: dict[str, list[tuple[int, TimedValue]]] = defaultdict(list)
    for row in rows:
        available = parse_utc(row["available_at"])
        priority = 1 if str(row["tag"]) == SHARE_TAGS[0] else 0
        grouped[str(row["cik"])].append((priority, TimedValue(available, float(row["value"]), available, f"sec:{row['tag']}")))
    result: dict[str, TimeIndex] = {}
    for cik, observations in grouped.items():
        ordered = [item for _priority, item in sorted(observations, key=lambda pair: (pair[1].available_at, pair[0]))]
        result[cik] = TimeIndex(ordered)
    return result


def load_provider_snapshot_indexes(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    tickers: Iterable[str],
    end: datetime,
    start: datetime | None = None,
) -> dict[str, TimeIndex]:
    db = quote_ident(database)
    if start is None:
        source_sql = f"""
SELECT upper(provider_ticker) AS ticker, symbol_id, market_cap, observed_at_utc, inserted_at,
  greatest(observed_at_utc, inserted_at) AS available_at
FROM {db}.{quote_ident(table)} FINAL
WHERE upper(provider_ticker) IN ({_in(tickers)}) AND market_cap > 0
  AND greatest(observed_at_utc, inserted_at) < parseDateTime64BestEffort({sql_string(end.isoformat())})
"""
    else:
        source_sql = f"""
SELECT ticker, selected_symbol_id AS symbol_id, selected_market_cap AS market_cap,
  selected_observed_at_utc AS observed_at_utc, selected_inserted_at AS inserted_at, available_at FROM (
  SELECT upper(provider_ticker) AS ticker,
    argMax(symbol_id, tuple(greatest(observed_at_utc, inserted_at), observed_at_utc, inserted_at)) AS selected_symbol_id,
    argMax(market_cap, tuple(greatest(observed_at_utc, inserted_at), observed_at_utc, inserted_at)) AS selected_market_cap,
    argMax(observed_at_utc, tuple(greatest(observed_at_utc, inserted_at), observed_at_utc, inserted_at)) AS selected_observed_at_utc,
    argMax(inserted_at, tuple(greatest(observed_at_utc, inserted_at), observed_at_utc, inserted_at)) AS selected_inserted_at,
    max(greatest(observed_at_utc, inserted_at)) AS available_at
  FROM {db}.{quote_ident(table)} FINAL
  WHERE upper(provider_ticker) IN ({_in(tickers)}) AND market_cap > 0
    AND greatest(observed_at_utc, inserted_at) < parseDateTime64BestEffort({sql_string(start.isoformat())})
  GROUP BY ticker
  UNION ALL
  SELECT upper(provider_ticker) AS ticker, symbol_id AS selected_symbol_id,
    market_cap AS selected_market_cap, observed_at_utc AS selected_observed_at_utc,
    inserted_at AS selected_inserted_at,
    greatest(observed_at_utc, inserted_at) AS available_at
  FROM {db}.{quote_ident(table)} FINAL
  WHERE upper(provider_ticker) IN ({_in(tickers)}) AND market_cap > 0
    AND greatest(observed_at_utc, inserted_at) >= parseDateTime64BestEffort({sql_string(start.isoformat())})
    AND greatest(observed_at_utc, inserted_at) < parseDateTime64BestEffort({sql_string(end.isoformat())})
)
"""
    rows = _json_rows(client, f"""
{source_sql}
ORDER BY ticker, available_at, observed_at_utc
FORMAT JSONEachRow
""")
    grouped: dict[str, list[TimedValue]] = defaultdict(list)
    for row in rows:
        symbol_id = str(row.get("symbol_id") or "")
        ticker = str(row["ticker"]).upper()
        value = TimedValue(
            available_at=parse_utc(row["available_at"]),
            observed_at=parse_utc(row["observed_at_utc"]),
            value=float(row["market_cap"]),
            source=f"provider_snapshot:{symbol_id or ticker}",
        )
        if symbol_id:
            grouped[f"symbol:{symbol_id}"].append(value)
        grouped[f"ticker:{ticker}"].append(value)
    return {key: TimeIndex(values) for key, values in grouped.items()}


def load_daily_close_indexes(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    tickers: Iterable[str],
    start: datetime,
    end: datetime,
) -> dict[str, TimeIndex]:
    db = quote_ident(database)
    lookback = start - timedelta(days=21)
    rows = _json_rows(client, f"""
SELECT upper(sym) AS ticker, bar_end, close
FROM {db}.{quote_ident(table)} FINAL
WHERE timeframe='1d' AND bar_family='trade' AND upper(sym) IN ({_in(tickers)})
  AND bar_end >= parseDateTime64BestEffort({sql_string(lookback.isoformat())})
  AND bar_end < parseDateTime64BestEffort({sql_string(end.isoformat())})
  AND close > 0
ORDER BY ticker, bar_end
FORMAT JSONEachRow
""")
    grouped: dict[str, list[TimedValue]] = defaultdict(list)
    for row in rows:
        bar_end = parse_utc(row["bar_end"])
        grouped[str(row["ticker"]).upper()].append(TimedValue(bar_end, float(row["close"]), bar_end, "prior_daily_close"))
    return {ticker: TimeIndex(values) for ticker, values in grouped.items()}


def summarize_article_caps(ticker_rows: Sequence[Mapping[str, Any]], ticker_count: int) -> dict[str, Any]:
    known = [row for row in ticker_rows if row.get("market_cap") is not None]
    values = [float(row["market_cap"]) for row in known]
    buckets = sorted({str(row["market_cap_bucket"]) for row in known})
    known_count = len(known)
    if ticker_count <= 0:
        coverage = "no_tickers"
    elif known_count == 0:
        coverage = "missing"
    elif known_count == ticker_count:
        coverage = "complete"
    else:
        coverage = "partial"
    max_age = max((float(row["market_cap_age_days"]) for row in known), default=None)
    return {
        "market_cap_coverage": coverage,
        "market_cap_known_ticker_count": known_count,
        "market_cap_missing_fraction": (ticker_count - known_count) / ticker_count if ticker_count else None,
        "market_cap_min": min(values) if values else None,
        "market_cap_median": median(values) if values else None,
        "market_cap_max": max(values) if values else None,
        "market_cap_min_bucket": cap_bucket(min(values)) if values else "missing",
        "market_cap_max_bucket": cap_bucket(max(values)) if values else "missing",
        "market_cap_bucket_set": "|".join(buckets) if buckets else "missing",
        "market_cap_all_same_bucket": bool(buckets) and len(buckets) == 1,
        "market_cap_contains_nano_micro": any(bucket.startswith(("nano_", "micro_")) for bucket in buckets),
        "market_cap_source_set": "|".join(sorted({str(row["market_cap_source"]) for row in known})) if known else "missing",
        "market_cap_max_age_days": max_age,
        "market_cap_max_age_bucket": age_bucket(max_age),
    }


def market_cap_paths(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({
        f"market_cap_coverage={row['market_cap_coverage']}",
        f"market_cap_min_bucket={row['market_cap_min_bucket']}",
        f"market_cap_max_bucket={row['market_cap_max_bucket']}",
        f"market_cap_bucket_set={row['market_cap_bucket_set']}",
        f"market_cap_all_same_bucket={str(bool(row['market_cap_all_same_bucket'])).lower()}",
        f"market_cap_contains_nano_micro={str(bool(row['market_cap_contains_nano_micro'])).lower()}",
        f"market_cap_source_set={row['market_cap_source_set']}",
        f"market_cap_max_age={row['market_cap_max_age_bucket']}",
    }))


def contextual_paths(row: Mapping[str, Any]) -> tuple[str, ...]:
    allowed = (
        "provider=", "ticker_count_bucket=", "session_segment=", "hour_et=", "weekday_et=",
        "any_ticker_first_session=", "all_tickers_first_session=", "min_session_ordinal=",
        "min_seconds_since_previous=", "any_ticker_news_within_5m=", "any_ticker_news_within_30m=",
        "update_delay=", "tag=", "channel=", "channel_pair=", "tag_channel=", "tag_set=",
        "channel_set=", "metadata_signature=", "text=", "rule:",
    )
    base = {feature for feature in feature_names(row) if feature.startswith(allowed)}
    base.add(f"rendered_chars_bucket={chars_bucket(int(row.get('rendered_chars') or 0))}")
    return tuple(sorted(base))


def add_count(target: dict[str, dict[str, Counts]], feature: str, split: str, label: str) -> None:
    target.setdefault(feature, {}).setdefault(split, Counts()).add(label)


def build_stats(rows: Sequence[Mapping[str, Any]]) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Counts]
]:
    cap_counts: dict[str, dict[str, Counts]] = {}
    interaction_counts: dict[str, dict[str, Counts]] = {}
    context_counts: dict[str, dict[str, Counts]] = {}
    cap_month: dict[str, dict[str, Counts]] = {}
    interaction_month: dict[str, dict[str, Counts]] = {}
    context_month: dict[str, dict[str, Counts]] = {}
    totals = {split: Counts() for split in SPLITS}
    for row in rows:
        split = str(row["split"])
        month = str(row["published_month"])
        label = str(row["label"])
        totals[split].add(label)
        cap_features = market_cap_paths(row)
        context_features = contextual_paths(row)
        for context_feature in context_features:
            add_count(context_counts, context_feature, split, label)
            add_count(context_month, context_feature, month, label)
        for cap_feature in cap_features:
            add_count(cap_counts, cap_feature, split, label)
            add_count(cap_month, cap_feature, month, label)
            for context_feature in context_features:
                interaction = f"market_cap_interaction={cap_feature} && {context_feature}"
                add_count(interaction_counts, interaction, split, label)
                add_count(interaction_month, interaction, month, label)
    return (
        feature_report_rows(cap_counts, cap_month, totals),
        feature_report_rows(interaction_counts, interaction_month, totals),
        feature_report_rows(context_counts, context_month, totals),
        totals,
    )


def annotate_interactions(
    rows: Sequence[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    context_by_feature = {str(row["feature"]): row for row in context_rows}
    annotated: list[dict[str, Any]] = []
    prefix = "market_cap_interaction="
    separator = " && "
    for row in rows:
        feature = str(row["feature"])
        if not feature.startswith(prefix) or separator not in feature:
            raise ValueError(f"invalid market-cap interaction feature: {feature}")
        cap_feature, context_feature = feature[len(prefix):].split(separator, 1)
        context = context_by_feature[context_feature]
        context_grade = candidate_grade(context)
        interaction_grade = candidate_grade(row)
        annotated.append({
            **row,
            "market_cap_feature": cap_feature,
            "context_feature": context_feature,
            "interaction_candidate_grade": interaction_grade,
            "context_candidate_grade": context_grade,
            "context_support": int(context["support"]),
            "context_eligible": int(context["eligible"]),
            "context_eligible_rate": context["eligible_rate"],
            "context_discovery_eligible_rate": context["discovery_eligible_rate"],
            "context_validation_eligible_rate": context["validation_eligible_rate"],
            "context_final_eligible_rate": context["final_eligible_rate"],
            "support_share_of_context": int(row["support"]) / int(context["support"]) if int(context["support"]) else None,
            "eligible_rate_delta_vs_context": (
                float(row["eligible_rate"]) - float(context["eligible_rate"])
                if row.get("eligible_rate") is not None and context.get("eligible_rate") is not None else None
            ),
        })
    return annotated


def select_interaction_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    size_prefixes = (
        "market_cap_min_bucket=", "market_cap_max_bucket=", "market_cap_bucket_set=",
        "market_cap_contains_nano_micro=",
    )
    selected: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("discovery_support") or 0) < 100:
            continue
        cap_feature = str(row["market_cap_feature"])
        if not cap_feature.startswith(size_prefixes):
            continue
        grade = str(row["interaction_candidate_grade"])
        context_grade = str(row["context_candidate_grade"])
        if grade not in {"high_precision_candidate", "promising_candidate", "audit_candidate"}:
            continue
        if grade in {"high_precision_candidate", "promising_candidate"}:
            if context_grade in {"high_precision_candidate", "promising_candidate"}:
                continue
            opening_class = "opened_precision_path"
        elif context_grade != "not_safe_for_rejection":
            continue
        else:
            opening_class = "opened_audit_path"
        selected.append({**row, "candidate_grade": grade, "opening_class": opening_class})
    selected.sort(key=lambda row: (
        {"high_precision_candidate": 0, "promising_candidate": 1, "audit_candidate": 2, "insufficient_forward_support": 3}[str(row["candidate_grade"])],
        -int(row["discovery_support"]), str(row["feature"]),
    ))
    return selected


def candidate_membership(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_cap: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_cap[str(candidate["market_cap_feature"])].append(candidate)
    output: list[dict[str, Any]] = []
    scope_counts: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    for row in rows:
        contexts = set(contextual_paths(row))
        matches: list[Mapping[str, Any]] = []
        for cap_feature in market_cap_paths(row):
            matches.extend(
                candidate for candidate in by_cap.get(cap_feature, ())
                if str(candidate["context_feature"]) in contexts
            )
        if not matches:
            continue
        matches.sort(key=lambda item: str(item["feature"]))
        split = str(row["split"])
        label = str(row["label"])
        scopes = {"all_candidates"}
        scopes.update(str(item["opening_class"]) for item in matches)
        scopes.update(str(item["candidate_grade"]) for item in matches)
        for scope in scopes:
            scope_counts[scope][split].add(label)
        output.append({
            "source_id": str(row["source_id"]),
            "published_at_utc": str(row["published_at_text"]),
            "split": split,
            "current_label": label,
            "tickers": list(row.get("tickers") or ()),
            "market_cap_coverage": str(row["market_cap_coverage"]),
            "market_cap_bucket_set": str(row["market_cap_bucket_set"]),
            "matched_candidate_count": len(matches),
            "matched_candidate_features": [str(item["feature"]) for item in matches],
            "matched_candidate_grades": sorted({str(item["candidate_grade"]) for item in matches}),
            "matched_opening_classes": sorted({str(item["opening_class"]) for item in matches}),
        })
    summary = {
        scope: {
            split: {
                "support": counts.total,
                "eligible": counts.eligible,
                "ineligible": counts.ineligible,
                "eligible_rate": counts.eligible / counts.decisive if counts.decisive else None,
            }
            for split, counts in by_split.items()
        }
        for scope, by_split in sorted(scope_counts.items())
    }
    return output, summary


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(month + "-01").replace(tzinfo=UTC)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def enrich_rows(
    rows_by_month: Mapping[str, Sequence[dict[str, Any]]],
    *,
    client: ClickHouseHttpClient,
    database: str,
    macro_database: str,
    macro_table: str,
    bridge_table: str,
    sec_table: str,
    snapshot_table: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows = [row for month_rows in rows_by_month.values() for row in month_rows]
    all_tickers = sorted({str(ticker).upper() for row in all_rows for ticker in row.get("tickers") or () if str(ticker).strip()})
    analysis_start = min(row["published_at_utc"] for row in all_rows)
    analysis_end = max(row["published_at_utc"] for row in all_rows) + timedelta(seconds=1)
    bridge = load_bridge_index(
        client, database=database, table=bridge_table, tickers=all_tickers,
        start=analysis_start.date(), end=analysis_end.date() + timedelta(days=1),
    )
    all_ciks = {str(item["cik"]) for values in bridge.values() for item in values if str(item.get("cik") or "")}
    shares = load_share_indexes(
        client, database=database, table=sec_table, ciks=all_ciks,
        start=analysis_start, end=analysis_end,
    )
    provider = load_provider_snapshot_indexes(
        client, database=database, table=snapshot_table, tickers=all_tickers, end=analysis_end,
    )

    enriched: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    coverage_by_split: dict[str, Counter[str]] = defaultdict(Counter)
    overlap = {"rows": 0, "same_bucket": 0, "within_one_bucket": 0, "log_ratio_abs_sum": 0.0}
    bucket_order = {
        "nano_lt_50m": 0, "micro_50m_300m": 1, "small_300m_2b": 2,
        "mid_2b_10b": 3, "large_10b_200b": 4, "mega_gte_200b": 5,
    }
    for month, month_rows in rows_by_month.items():
        month_start, month_end = _month_bounds(month)
        month_tickers = {str(ticker).upper() for row in month_rows for ticker in row.get("tickers") or () if str(ticker).strip()}
        closes = load_daily_close_indexes(
            client, database=macro_database, table=macro_table, tickers=month_tickers,
            start=month_start, end=month_end,
        )
        for row in month_rows:
            published = row["published_at_utc"]
            ticker_rows: list[dict[str, Any]] = []
            for raw_ticker in row.get("tickers") or ():
                ticker = str(raw_ticker).upper()
                bridge_row = resolve_bridge(bridge, ticker, published.date())
                symbol_key = f"symbol:{bridge_row.get('symbol_id')}" if bridge_row and bridge_row.get("symbol_id") else ""
                provider_index = provider.get(symbol_key) if symbol_key else None
                provider_lookup = "symbol_id" if provider_index else "ticker_fallback"
                provider_index = provider_index or provider.get(f"ticker:{ticker}")
                provider_value = provider_index.before(published) if provider_index else None
                share_value = shares.get(str(bridge_row.get("cik"))).before(published) if bridge_row and shares.get(str(bridge_row.get("cik"))) else None
                close_value = closes.get(ticker).before(published) if closes.get(ticker) else None
                derived = share_value.value * close_value.value if share_value and close_value else None
                selected = provider_value.value if provider_value else derived
                source = "provider_snapshot" if provider_value else "derived_sec_shares_prior_close" if derived else "missing"
                available_at = provider_value.available_at if provider_value else max(share_value.available_at, close_value.available_at) if share_value and close_value else None
                age_days = (published - available_at).total_seconds() / 86400 if available_at else None
                ticker_row = {
                    "ticker": ticker,
                    "market_cap": selected,
                    "market_cap_bucket": cap_bucket(selected),
                    "market_cap_source": source,
                    "market_cap_available_at_utc": available_at.isoformat() if available_at else None,
                    "market_cap_age_days": age_days,
                    "provider_market_cap": provider_value.value if provider_value else None,
                    "provider_market_cap_lookup": provider_lookup if provider_value else None,
                    "derived_market_cap": derived,
                    "shares_available_at_utc": share_value.available_at.isoformat() if share_value else None,
                    "prior_close_available_at_utc": close_value.available_at.isoformat() if close_value else None,
                    "identity_cik": str(bridge_row.get("cik")) if bridge_row else None,
                }
                ticker_rows.append(ticker_row)
                source_counts[source] += 1
                if provider_value and derived and provider_value.value > 0 and derived > 0:
                    left = cap_bucket(provider_value.value)
                    right = cap_bucket(derived)
                    overlap["rows"] += 1
                    overlap["same_bucket"] += int(left == right)
                    overlap["within_one_bucket"] += int(abs(bucket_order[left] - bucket_order[right]) <= 1)
                    overlap["log_ratio_abs_sum"] += abs(math.log(derived / provider_value.value))
            summary = summarize_article_caps(ticker_rows, int(row.get("ticker_count") or 0))
            merged = {**row, **summary, "market_cap_tickers": ticker_rows}
            enriched.append(merged)
            coverage_by_split[str(row["split"])][str(summary["market_cap_coverage"])] += 1
    overlap_rows = int(overlap["rows"])
    summary = {
        "analysis_tickers": len(all_tickers),
        "identity_bridge_tickers": len(bridge),
        "share_ciks": len(shares),
        "provider_snapshot_tickers": sum(key.startswith("ticker:") for key in provider),
        "provider_snapshot_symbols": sum(key.startswith("symbol:") for key in provider),
        "ticker_source_counts": dict(source_counts),
        "article_coverage_by_split": {split: dict(counts) for split, counts in coverage_by_split.items()},
        "provider_derived_overlap": {
            "rows": overlap_rows,
            "same_bucket_rate": overlap["same_bucket"] / overlap_rows if overlap_rows else None,
            "within_one_bucket_rate": overlap["within_one_bucket"] / overlap_rows if overlap_rows else None,
            "geometric_mean_absolute_factor": math.exp(overlap["log_ratio_abs_sum"] / overlap_rows) if overlap_rows else None,
        },
    }
    enriched.sort(key=lambda row: (row["published_at_utc"], row["source_id"]))
    return enriched, summary


def write_enriched(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    keep = (
        "source_id", "label", "split", "published_at_text", "provider", "tickers", "ticker_count",
        "provider_tags", "channels", "rendered_chars", "session_segment", "hour_et", "weekday_et",
        "any_ticker_first_session", "all_tickers_first_session", "min_ticker_session_ordinal",
        "min_seconds_since_previous_ticker_news", "any_ticker_news_within_5m", "any_ticker_news_within_30m",
        "market_cap_coverage", "market_cap_known_ticker_count", "market_cap_missing_fraction",
        "market_cap_min", "market_cap_median", "market_cap_max", "market_cap_min_bucket",
        "market_cap_max_bucket", "market_cap_bucket_set", "market_cap_all_same_bucket",
        "market_cap_contains_nano_micro", "market_cap_source_set", "market_cap_max_age_days",
        "market_cap_max_age_bucket", "market_cap_tickers",
    )
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json({key: row.get(key) for key in keep}) + "\n")


def write_jsonl_new(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def build_markdown(report: Mapping[str, Any]) -> str:
    coverage_lines = []
    for split in SPLITS:
        counts = report["coverage"]["article_coverage_by_split"].get(split, {})
        coverage_lines.append(
            f"| {split} | {counts.get('complete', 0):,} | {counts.get('partial', 0):,} | "
            f"{counts.get('missing', 0):,} | {counts.get('no_tickers', 0):,} |"
        )
    candidate_lines = []
    for row in report["top_candidates"][:30]:
        candidate_lines.append(
            f"| `{row['feature']}` | {row['candidate_grade']} | {int(row['support']):,} | "
            f"{100*float(row['eligible_rate'] or 0):.2f}% | {100*float(row.get('final_eligible_rate') or 0):.2f}% |"
        )
    overlap = report["coverage"]["provider_derived_overlap"]
    union_lines = []
    for scope in ("opened_precision_path", "opened_audit_path", "all_candidates"):
        by_split = report.get("candidate_union", {}).get(scope, {})
        support = sum(int(item.get("support") or 0) for item in by_split.values())
        eligible = sum(int(item.get("eligible") or 0) for item in by_split.values())
        union_lines.append(
            f"| {scope} | {support:,} | {eligible:,} | "
            f"{100*eligible/support:.2f}% |" if support else f"| {scope} | 0 | 0 | n/a |"
        )
    return "\n".join((
        "# News Synthesis Market-Cap Context Analysis", "",
        f"Analysis version: `{report['analysis_version']}`", "",
        "This is research evidence, not a production rejection authority. Market cap is selected strictly from information available before each article.", "",
        "## Causal authority", "",
        "- Preferred: provider-published market-cap snapshot with both observation and insertion time before publication.",
        "- Fallback: latest SEC-reported common shares available before publication multiplied by the latest completed daily close.",
        "- Missing identity, shares, or price remains explicit; no present-day value is substituted.", "",
        "## Article coverage", "", "| Split | Complete | Partial | Missing | No tickers |", "|---|---:|---:|---:|---:|",
        *coverage_lines, "",
        f"Provider/derived overlap: {int(overlap['rows']):,} ticker-article observations; "
        f"same six-band bucket {100*float(overlap['same_bucket_rate'] or 0):.2f}%; "
        f"within one bucket {100*float(overlap['within_one_bucket_rate'] or 0):.2f}%.", "",
        "## Deduplicated candidate-article union", "",
        "| Scope | Articles | Currently eligible exceptions | Eligible rate |", "|---|---:|---:|---:|",
        *union_lines, "",
        "## Strongest statistically gated interactions", "",
        "| Interaction | Grade | Support | Eligible rate | Final eligible rate |", "|---|---|---:|---:|---:|",
        *(candidate_lines or ["| none | insufficient | 0 | n/a | n/a |"]), "",
        "## Interpretation boundary", "",
        "- The 2025-2026 labels contain known provisional risk.",
        "- The previous 1,000-article holdout is already observed and was not reused for accuracy claims.",
        "- Derived capitalization may be distorted for ADR ratios, multiple share classes, stale filings, or corporate actions; provenance and age must remain in every downstream rule.",
        "- Candidate paths require blind exception review and a fresh holdout before production promotion.", "",
    ))


def run_analysis(
    *,
    article_features: Path = DEFAULT_ARTICLE_FEATURES,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    database: str = DEFAULT_DATABASE,
    macro_database: str = DEFAULT_MACRO_DATABASE,
    macro_table: str = DEFAULT_MACRO_TABLE,
    bridge_table: str = DEFAULT_BRIDGE_TABLE,
    sec_table: str = DEFAULT_SEC_TABLE,
    snapshot_table: str = DEFAULT_SNAPSHOT_TABLE,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite market-cap analysis: {output_root}")
    output_root.mkdir(parents=True)
    rows, rows_by_month = load_articles(article_features)
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(),
        timeout_seconds=300, persistent=True,
        default_query_params={"max_execution_time": 300, "max_threads": 6, "max_memory_usage": "12G"},
    )
    try:
        enriched, coverage = enrich_rows(
            rows_by_month, client=client, database=database, macro_database=macro_database,
            macro_table=macro_table, bridge_table=bridge_table, sec_table=sec_table,
            snapshot_table=snapshot_table,
        )
    finally:
        client.close()
    cap_stats, raw_interaction_stats, context_stats, split_totals = build_stats(enriched)
    interaction_stats = annotate_interactions(raw_interaction_stats, context_stats)
    candidates = select_interaction_candidates(interaction_stats)
    candidate_articles, candidate_union = candidate_membership(enriched, candidates)

    article_path = output_root / "ARTICLE_MARKET_CAP_FEATURES.jsonl"
    cap_path = output_root / "MARKET_CAP_PATH_STRENGTH.csv"
    interaction_path = output_root / "MARKET_CAP_INTERACTION_STRENGTH.csv"
    context_path = output_root / "CONTEXT_PATH_STRENGTH.csv"
    candidate_path = output_root / "CANDIDATE_INTERACTIONS.csv"
    candidate_article_path = output_root / "CANDIDATE_ARTICLES.jsonl"
    write_enriched(article_path, enriched)
    write_csv_new(cap_path, cap_stats)
    write_csv_new(interaction_path, interaction_stats)
    write_csv_new(context_path, context_stats)
    write_csv_new(candidate_path, candidates)
    write_jsonl_new(candidate_article_path, candidate_articles)

    report = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inputs": {"article_features": str(article_features), "article_features_sha256": sha256_path(article_features)},
        "population": {
            "articles": len(enriched),
            "splits": {split: {"total": value.total, "eligible": value.eligible, "ineligible": value.ineligible} for split, value in split_totals.items()},
        },
        "coverage": coverage,
        "market_cap_path_rows": len(cap_stats),
        "interaction_path_rows": len(interaction_stats),
        "context_path_rows": len(context_stats),
        "candidate_rows": len(candidates),
        "candidate_grades": dict(Counter(str(row["candidate_grade"]) for row in candidates)),
        "candidate_article_rows": len(candidate_articles),
        "candidate_union": candidate_union,
        "top_candidates": candidates[:100],
        "limitations": [
            "Labels are development supervision and not assumed error-free.",
            "The provider snapshot authority begins in May 2026; earlier coverage uses explicitly marked SEC-shares-times-prior-close estimates.",
            "The previous 1,000-article holdout is already observed and is excluded from any new accuracy claim.",
            "Every statistical candidate requires blind review and fresh held-out evaluation before production use.",
        ],
    }
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    write_json_new(report_path, report)
    with report_md_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(build_markdown(report))

    validation = {
        "status": "passed",
        "analysis_version": ANALYSIS_VERSION,
        "article_rows": len(enriched),
        "unique_source_ids": len({str(row["source_id"]) for row in enriched}),
        "source_ids_unique": len(enriched) == len({str(row["source_id"]) for row in enriched}),
        "exact_splits_present": {str(row["split"]) for row in enriched} == set(SPLITS),
        "positive_known_market_caps": all(
            float(ticker_row["market_cap"]) > 0
            for row in enriched for ticker_row in row["market_cap_tickers"]
            if ticker_row.get("market_cap") is not None
        ),
        "causal_nonnegative_ages": all(
            float(ticker_row["market_cap_age_days"]) >= 0
            for row in enriched for ticker_row in row["market_cap_tickers"]
            if ticker_row.get("market_cap_age_days") is not None
        ),
        "outputs_exist": all(path.exists() for path in (article_path, cap_path, interaction_path, context_path, candidate_path, candidate_article_path, report_path, report_md_path)),
    }
    if not all(value for key, value in validation.items() if key not in {"status", "analysis_version", "article_rows", "unique_source_ids"}):
        raise ValueError(f"market-cap analysis validation failed: {validation}")
    validation_path = output_root / "VALIDATION.json"
    write_json_new(validation_path, validation)
    write_json_new(output_root / "HASH_MANIFEST.json", {
        "analysis_version": ANALYSIS_VERSION,
        "inputs": report["inputs"],
        "outputs": {
            path.name: {"sha256": sha256_path(path), "bytes": path.stat().st_size}
            for path in (article_path, cap_path, interaction_path, context_path, candidate_path, candidate_article_path, report_path, report_md_path, validation_path)
        },
    })
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze causal market-cap context and interactions for News Synthesis filtering.")
    parser.add_argument("--article-features", type=Path, default=DEFAULT_ARTICLE_FEATURES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--macro-database", default=DEFAULT_MACRO_DATABASE)
    args = parser.parse_args(list(argv) if argv is not None else None)
    load_env_files(discover_env_files(Path.cwd()))
    report = run_analysis(
        article_features=args.article_features, output_root=args.output_root,
        database=args.database, macro_database=args.macro_database,
    )
    print(
        f"{report['analysis_version']} complete | articles={report['population']['articles']:,} "
        f"interactions={report['interaction_path_rows']:,} candidates={report['candidate_rows']:,} "
        f"output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
