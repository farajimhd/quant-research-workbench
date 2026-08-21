from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo


ANALYSIS_VERSION = "news_synthesis_provider_filter_analysis_v2"
NEW_YORK = ZoneInfo("America/New_York")
DECISIVE_LABELS = frozenset(("eligible", "ineligible"))
SPLITS = ("discovery_2025", "validation_2026_jan_apr", "final_2026_may_aug")

DEFAULT_AUTHORITY_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v1"
)
DEFAULT_METADATA_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_metadata_combination_distribution_v2"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v2"
)


TEXT_PATTERNS: dict[str, re.Pattern[str]] = {
    "halt": re.compile(r"\b(?:trading\s+)?halts?|halted|resum(?:e|es|ed|ing)|circuit breaker\b", re.I),
    "analyst_rating": re.compile(
        r"\b(?:analyst|price target|rating|upgrade[sd]?|downgrade[sd]?|"
        r"initiates?|maintains?|reiterates?|outperform|underperform|overweight|underweight)\b",
        re.I,
    ),
    "price_target": re.compile(r"\b(?:price target|price objective|\bPT\b)\b", re.I),
    "earnings_preview": re.compile(
        r"\b(?:earnings preview|ahead of earnings|what to expect|will report|"
        r"scheduled to report|before the bell|after the bell)\b",
        re.I,
    ),
    "why_moving": re.compile(
        r"\b(?:why (?:is|are|did)|what(?:'s| is) going on with)\b.{0,100}"
        r"\b(?:stock|shares?)\b|\bshares? (?:are )?trading (?:higher|lower|up|down)\b",
        re.I,
    ),
    "list_or_screener": re.compile(
        r"\b(?:stocks? to watch|top \d+|\d+ stocks?|screeners?|gainers?|losers?|"
        r"movers?|52-week highs?|52-week lows?)\b",
        re.I,
    ),
    "market_recap": re.compile(
        r"\b(?:market (?:wrap|recap|overview|update)|morning capsule|midday market update|"
        r"market close recap|closing bell|opening bell)\b",
        re.I,
    ),
    "technical_or_valuation": re.compile(
        r"\b(?:technical analysis|support level|resistance level|moving average|RSI|"
        r"valuation|price-to-earnings|P/E ratio|overvalued|undervalued)\b",
        re.I,
    ),
    "short_interest": re.compile(r"\b(?:short interest|short squeeze|days to cover)\b", re.I),
    "index_or_listing": re.compile(
        r"\b(?:index inclusion|added to (?:the )?(?:S&P|Russell|Nasdaq)|"
        r"listing notice|regains? compliance|minimum bid|reverse (?:stock )?split)\b",
        re.I,
    ),
    "macro": re.compile(
        r"\b(?:CPI|inflation|Federal Reserve|Fed decision|GDP|jobless claims|"
        r"nonfarm payrolls?|treasury yields?|consumer confidence)\b",
        re.I,
    ),
    "material_event": re.compile(
        r"\b(?:acquir(?:e|es|ed|ing)|merger|definitive agreement|financing|offering|"
        r"guidance|outlook|reports? (?:quarterly|annual|Q[1-4])|earnings results?|"
        r"FDA|clinical trial|primary endpoint|regulatory approval|complete response letter|"
        r"lawsuit|settlement|contract award|appoints?|resigns?|launches?|recall|"
        r"bankruptcy|restructuring|dividend|buyback)\b",
        re.I,
    ),
}

RULE_FEATURES = frozenset(
    (
        "rule:halt_without_material_override",
        "rule:analyst_rating_without_material_override",
        "rule:price_target_without_material_override",
        "rule:earnings_preview_without_material_override",
        "rule:why_moving_without_material_override",
        "rule:list_or_screener_without_material_override",
        "rule:market_recap_without_material_override",
        "rule:technical_or_valuation_without_material_override",
        "rule:short_interest_without_material_override",
        "rule:index_or_listing_without_material_override",
        "rule:macro_without_material_override",
    )
)


@dataclass(slots=True)
class Counts:
    total: int = 0
    eligible: int = 0
    ineligible: int = 0
    insufficient: int = 0

    def add(self, label: str) -> None:
        self.total += 1
        if label == "eligible":
            self.eligible += 1
        elif label == "ineligible":
            self.ineligible += 1
        else:
            self.insufficient += 1

    @property
    def decisive(self) -> int:
        return self.eligible + self.ineligible


@dataclass(frozen=True, slots=True)
class InputPaths:
    labels: Path
    metadata: Path
    rendered_texts: Path
    authority_manifest: Path
    authority_hash_manifest: Path
    metadata_hash_manifest: Path


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}: {exc}") from exc


def sha256_path(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def normalize_values(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip().casefold() for value in values if str(value).strip()}))


def parse_utc(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def analysis_split(timestamp: datetime) -> str | None:
    if timestamp.year == 2025:
        return "discovery_2025"
    if timestamp.year == 2026 and 1 <= timestamp.month <= 4:
        return "validation_2026_jan_apr"
    if timestamp.year == 2026 and 5 <= timestamp.month <= 8:
        return "final_2026_may_aug"
    return None


def session_segment(timestamp: datetime) -> str:
    local = timestamp.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    if minute < 4 * 60:
        return "overnight"
    if minute < 9 * 60 + 30:
        return "premarket"
    if minute < 16 * 60:
        return "regular"
    if minute < 20 * 60:
        return "after_hours"
    return "overnight"


def session_date(timestamp: datetime) -> str:
    return timestamp.astimezone(NEW_YORK).date().isoformat()


def seconds_bucket(value: float | None) -> str:
    if value is None:
        return "none"
    if value < 5 * 60:
        return "lt_5m"
    if value < 15 * 60:
        return "5_15m"
    if value < 30 * 60:
        return "15_30m"
    if value < 60 * 60:
        return "30_60m"
    if value < 4 * 60 * 60:
        return "1_4h"
    return "gte_4h"


def ordinal_bucket(value: int) -> str:
    if value <= 1:
        return "1"
    if value <= 3:
        return "2_3"
    if value <= 5:
        return "4_5"
    if value <= 10:
        return "6_10"
    return "gt_10"


def ticker_count_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 5:
        return "3_5"
    if value <= 10:
        return "6_10"
    return "gt_10"


def extract_title(rendered_text: str) -> str:
    first = rendered_text.splitlines()[0].strip() if rendered_text else ""
    return first[6:].strip() if first.casefold().startswith("title:") else first


def text_flags(rendered_text: str) -> dict[str, bool]:
    title = extract_title(rendered_text)
    bounded = rendered_text[:12_000]
    result = {
        name: bool(pattern.search(title if name in {
            "earnings_preview", "why_moving", "list_or_screener", "market_recap"
        } else bounded))
        for name, pattern in TEXT_PATTERNS.items()
    }
    result["question_title"] = "?" in title
    result["title_only"] = "\n" not in rendered_text.strip()
    return result


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def mutual_information_binary(
    feature_eligible: int,
    feature_ineligible: int,
    total_eligible: int,
    total_ineligible: int,
) -> float:
    cells = (
        (feature_eligible, feature_eligible + feature_ineligible, total_eligible),
        (feature_ineligible, feature_eligible + feature_ineligible, total_ineligible),
        (total_eligible - feature_eligible, total_eligible + total_ineligible - feature_eligible - feature_ineligible, total_eligible),
        (total_ineligible - feature_ineligible, total_eligible + total_ineligible - feature_eligible - feature_ineligible, total_ineligible),
    )
    total = total_eligible + total_ineligible
    value = 0.0
    for observed, feature_margin, label_margin in cells:
        if observed <= 0 or feature_margin <= 0 or label_margin <= 0:
            continue
        probability = observed / total
        expected_probability = (feature_margin / total) * (label_margin / total)
        value += probability * math.log(probability / expected_probability, 2)
    return value


def odds_ratio(
    feature_eligible: int,
    feature_ineligible: int,
    total_eligible: int,
    total_ineligible: int,
) -> float:
    # Haldane-Anscombe correction makes zero-cell rules finite and comparable.
    a = feature_ineligible + 0.5
    b = feature_eligible + 0.5
    c = total_ineligible - feature_ineligible + 0.5
    d = total_eligible - feature_eligible + 0.5
    return (a * d) / (b * c)


def _expected_input_paths(authority_root: Path, metadata_root: Path) -> InputPaths:
    manifest_path = authority_root / "LOAD_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return InputPaths(
        labels=Path(manifest["primary_tables"]["article_forecast_eligibility"]["path"]),
        metadata=metadata_root / "article_metadata_labels.jsonl",
        rendered_texts=Path(manifest["external_source_text_authority"]["path"]),
        authority_manifest=manifest_path,
        authority_hash_manifest=authority_root / "HASH_MANIFEST.json",
        metadata_hash_manifest=metadata_root / "HASH_MANIFEST.json",
    )


def verify_inputs(paths: InputPaths) -> dict[str, Any]:
    authority_manifest = json.loads(paths.authority_manifest.read_text(encoding="utf-8"))
    authority_hashes = json.loads(paths.authority_hash_manifest.read_text(encoding="utf-8"))
    metadata_hashes = json.loads(paths.metadata_hash_manifest.read_text(encoding="utf-8"))
    expected_rendered = authority_manifest["external_source_text_authority"]["sha256"]
    expected_metadata = metadata_hashes["outputs"]["article_metadata_labels.jsonl"]["sha256"]
    expected_labels = None
    for section in ("outputs", "files", "artifacts"):
        row = authority_hashes.get(section, {}).get("article_forecast_eligibility_labels.jsonl")
        if isinstance(row, Mapping):
            expected_labels = row.get("sha256")
            break
    actual = {
        "labels": sha256_path(paths.labels),
        "metadata": sha256_path(paths.metadata),
        "rendered_texts": sha256_path(paths.rendered_texts),
    }
    if expected_labels and actual["labels"] != expected_labels:
        raise ValueError("current label authority SHA-256 mismatch")
    if actual["metadata"] != expected_metadata:
        raise ValueError("metadata authority SHA-256 mismatch")
    if actual["rendered_texts"] != expected_rendered:
        raise ValueError("rendered-text authority SHA-256 mismatch")
    return {
        "actual_sha256": actual,
        "expected_sha256": {
            "labels": expected_labels,
            "metadata": expected_metadata,
            "rendered_texts": expected_rendered,
        },
    }


def load_current_labels(path: Path) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
    labels: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for row in iter_jsonl(path):
        source_id = str(row["source_id"])
        if source_id in labels:
            raise ValueError(f"duplicate current label source_id: {source_id}")
        label = str(row["forecast_eligibility_label"])
        labels[source_id] = {
            "label": label,
            "authority_class": str(row.get("authority_class") or ""),
            "certification_level": str(row.get("certification_level") or ""),
            "human_certified": bool(row.get("human_certified")),
            "source_dataset": str(row.get("source_dataset") or ""),
        }
        counts[label] += 1
    return labels, counts


def load_analysis_rows(
    metadata_path: Path,
    labels: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metadata_ids: set[str] = set()
    ignored_outside_period = 0
    excluded_insufficient = 0
    for metadata in iter_jsonl(metadata_path):
        source_id = str(metadata["source_id"])
        if source_id in metadata_ids:
            raise ValueError(f"duplicate metadata source_id: {source_id}")
        metadata_ids.add(source_id)
        if source_id not in labels:
            raise ValueError(f"metadata source_id absent from current authority: {source_id}")
        timestamp = parse_utc(str(metadata["published_at_utc"]))
        split = analysis_split(timestamp)
        if split is None:
            ignored_outside_period += 1
            continue
        current = labels[source_id]
        if str(current["label"]) not in DECISIVE_LABELS:
            excluded_insufficient += 1
            continue
        tickers = tuple(sorted({str(value).strip().upper() for value in metadata.get("tickers") or () if str(value).strip()}))
        tags = normalize_values(metadata.get("provider_tags") or ())
        channels = normalize_values(metadata.get("channels") or ())
        update_value = str(metadata.get("metadata_last_updated_at_utc") or "").strip()
        update_delay = None
        if update_value:
            update_delay = max(0.0, (parse_utc(update_value) - timestamp).total_seconds())
        rows.append({
            "source_id": source_id,
            "label": str(current["label"]),
            "authority_class": str(current["authority_class"]),
            "certification_level": str(current["certification_level"]),
            "human_certified": bool(current["human_certified"]),
            "source_dataset": str(current["source_dataset"]),
            "published_at_utc": timestamp,
            "published_at_text": timestamp.isoformat(),
            "published_month": timestamp.strftime("%Y-%m"),
            "split": split,
            "provider": str(metadata.get("provider") or "").strip().casefold(),
            "tickers": tickers,
            "ticker_count": len(tickers),
            "provider_tags": tags,
            "channels": channels,
            "content_quality_flags": normalize_values(metadata.get("content_quality_flags") or ()),
            "update_delay_seconds": update_delay,
            "session_segment": session_segment(timestamp),
            "session_date": session_date(timestamp),
            "hour_et": timestamp.astimezone(NEW_YORK).hour,
            "weekday_et": timestamp.astimezone(NEW_YORK).strftime("%a").casefold(),
        })
    if metadata_ids != set(labels):
        raise ValueError(
            f"metadata/current-label membership mismatch missing={len(set(labels)-metadata_ids)} "
            f"extra={len(metadata_ids-set(labels))}"
        )
    rows.sort(key=lambda row: (row["published_at_utc"], row["source_id"]))
    return rows, {
        "metadata_ids": len(metadata_ids),
        "analysis_rows": len(rows),
        "ignored_outside_2025_2026": ignored_outside_period,
        "excluded_insufficient_2025_2026": excluded_insufficient,
    }


def attach_ticker_history(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    last_timestamp: dict[str, datetime] = {}
    session_counts: dict[tuple[str, str], int] = defaultdict(int)
    ticker_links = 0
    index = 0
    while index < len(rows):
        timestamp = rows[index]["published_at_utc"]
        end = index + 1
        while end < len(rows) and rows[end]["published_at_utc"] == timestamp:
            end += 1
        # Every row in an equal-timestamp group sees the same strictly-prior
        # state. Source-id ordering makes output deterministic but cannot make
        # one simultaneous publication causally available to another.
        for row in rows[index:end]:
            current_session = row["session_date"]
            previous_seconds: list[float] = []
            ordinals: list[int] = []
            for ticker in row["tickers"]:
                ticker_links += 1
                previous = last_timestamp.get(ticker)
                if previous is not None:
                    previous_seconds.append(max(0.0, (timestamp - previous).total_seconds()))
                ordinals.append(session_counts[(ticker, current_session)] + 1)
            row["any_ticker_first_session"] = bool(ordinals) and any(value == 1 for value in ordinals)
            row["all_tickers_first_session"] = bool(ordinals) and all(value == 1 for value in ordinals)
            row["min_ticker_session_ordinal"] = min(ordinals) if ordinals else 0
            row["max_ticker_session_ordinal"] = max(ordinals) if ordinals else 0
            row["min_seconds_since_previous_ticker_news"] = min(previous_seconds) if previous_seconds else None
            row["max_seconds_since_previous_ticker_news"] = max(previous_seconds) if previous_seconds else None
            row["any_ticker_news_within_5m"] = any(value < 300 for value in previous_seconds)
            row["any_ticker_news_within_30m"] = any(value < 1800 for value in previous_seconds)
        for row in rows[index:end]:
            for ticker in row["tickers"]:
                last_timestamp[ticker] = timestamp
                session_counts[(ticker, row["session_date"])] += 1
        index = end
    return {
        "ticker_links": ticker_links,
        "unique_tickers": len(last_timestamp),
        "unique_ticker_sessions": len(session_counts),
    }


def load_text_flags(
    rendered_path: Path,
    wanted_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    flags: dict[str, dict[str, Any]] = {}
    total_rows = 0
    for row in iter_jsonl(rendered_path):
        total_rows += 1
        source_id = str(row["source_id"])
        if source_id not in wanted_ids:
            continue
        if source_id in flags:
            raise ValueError(f"duplicate rendered source_id: {source_id}")
        text = str(row.get("rendered_text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered text hash mismatch: {source_id}")
        flags[source_id] = {
            **text_flags(text),
            "rendered_chars": len(text),
            "rendered_text_hash": digest,
        }
    missing = wanted_ids - set(flags)
    if missing:
        raise ValueError(f"missing rendered rows for analysis population: {len(missing)}")
    return flags, {
        "rendered_authority_rows": total_rows,
        "analysis_rendered_rows": len(flags),
        "missing_analysis_rows": 0,
    }


def attach_text_flags(rows: Sequence[dict[str, Any]], flags: Mapping[str, Mapping[str, Any]]) -> None:
    for row in rows:
        row.update(flags[row["source_id"]])


def metadata_signature(row: Mapping[str, Any]) -> str:
    return canonical_json({
        "provider": row["provider"],
        "ticker_count_bucket": ticker_count_bucket(int(row["ticker_count"])),
        "provider_tags": row["provider_tags"],
        "channels": row["channels"],
    })


def feature_names(row: Mapping[str, Any]) -> tuple[str, ...]:
    names: set[str] = {
        f"provider={row['provider'] or 'missing'}",
        f"ticker_count_bucket={ticker_count_bucket(int(row['ticker_count']))}",
        f"session_segment={row['session_segment']}",
        f"hour_et={row['hour_et']}",
        f"weekday_et={row['weekday_et']}",
        f"any_ticker_first_session={str(bool(row['any_ticker_first_session'])).lower()}",
        f"all_tickers_first_session={str(bool(row['all_tickers_first_session'])).lower()}",
        f"min_session_ordinal={ordinal_bucket(int(row['min_ticker_session_ordinal'] or 0))}",
        f"min_seconds_since_previous={seconds_bucket(row['min_seconds_since_previous_ticker_news'])}",
        f"any_ticker_news_within_5m={str(bool(row['any_ticker_news_within_5m'])).lower()}",
        f"any_ticker_news_within_30m={str(bool(row['any_ticker_news_within_30m'])).lower()}",
        f"metadata_signature={metadata_signature(row)}",
        f"human_certified={str(bool(row['human_certified'])).lower()}",
        f"authority_class={row['authority_class'] or 'missing'}",
    }
    update_delay = row.get("update_delay_seconds")
    names.add(f"update_delay={seconds_bucket(float(update_delay) if update_delay is not None else None)}")
    for tag in row["provider_tags"]:
        names.add(f"tag={tag}")
    for channel in row["channels"]:
        names.add(f"channel={channel}")
    for left, right in combinations(row["channels"], 2):
        names.add(f"channel_pair={left}|{right}")
    for tag in row["provider_tags"]:
        for channel in row["channels"]:
            names.add(f"tag_channel={tag}|{channel}")
    if row["provider_tags"]:
        names.add("tag_set=" + "|".join(row["provider_tags"]))
    if row["channels"]:
        names.add("channel_set=" + "|".join(row["channels"]))
    for name in (*TEXT_PATTERNS, "question_title", "title_only"):
        if bool(row.get(name)):
            names.add(f"text={name}")
    material = bool(row.get("material_event"))
    for family in (
        "halt", "analyst_rating", "price_target", "earnings_preview", "why_moving",
        "list_or_screener", "market_recap", "technical_or_valuation", "short_interest",
        "index_or_listing", "macro",
    ):
        metadata_match = family == "halt" and (
            "halts" in row["provider_tags"] or "halts" in row["channels"]
        )
        if (bool(row.get(family)) or metadata_match) and not material:
            names.add(f"rule:{family}_without_material_override")
    return tuple(sorted(names))


def aggregate_features(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Counts]],
    dict[str, dict[str, Counts]],
    dict[str, Counts],
    dict[str, Counts],
]:
    by_feature: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    by_feature_month: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    split_totals: dict[str, Counts] = defaultdict(Counts)
    month_totals: dict[str, Counts] = defaultdict(Counts)
    for row in rows:
        label = str(row["label"])
        split = str(row["split"])
        month = str(row["published_month"])
        split_totals[split].add(label)
        month_totals[month].add(label)
        for feature in feature_names(row):
            by_feature[feature][split].add(label)
            by_feature_month[feature][month].add(label)
    return by_feature, by_feature_month, split_totals, month_totals


def feature_category(feature: str) -> str:
    return feature.split("=", 1)[0].split(":", 1)[0]


def feature_report_rows(
    by_feature: Mapping[str, Mapping[str, Counts]],
    by_feature_month: Mapping[str, Mapping[str, Counts]],
    split_totals: Mapping[str, Counts],
) -> list[dict[str, Any]]:
    total_eligible = sum(split_totals[split].eligible for split in SPLITS)
    total_ineligible = sum(split_totals[split].ineligible for split in SPLITS)
    total_decisive = total_eligible + total_ineligible
    reports: list[dict[str, Any]] = []
    for feature, split_rows in by_feature.items():
        all_counts = Counts()
        report: dict[str, Any] = {"feature": feature, "category": feature_category(feature)}
        for split in SPLITS:
            counts = split_rows.get(split, Counts())
            all_counts.total += counts.total
            all_counts.eligible += counts.eligible
            all_counts.ineligible += counts.ineligible
            all_counts.insufficient += counts.insufficient
            low, high = wilson_interval(counts.eligible, counts.decisive)
            prefix = split.replace("_2026_jan_apr", "").replace("_2026_may_aug", "").replace("_2025", "")
            report[f"{prefix}_support"] = counts.total
            report[f"{prefix}_eligible"] = counts.eligible
            report[f"{prefix}_ineligible"] = counts.ineligible
            report[f"{prefix}_eligible_rate"] = counts.eligible / counts.decisive if counts.decisive else None
            report[f"{prefix}_eligible_rate_ci_low"] = low
            report[f"{prefix}_eligible_rate_ci_high"] = high
        low, high = wilson_interval(all_counts.eligible, all_counts.decisive)
        report.update({
            "support": all_counts.total,
            "eligible": all_counts.eligible,
            "ineligible": all_counts.ineligible,
            "insufficient": all_counts.insufficient,
            "coverage": all_counts.total / sum(row.total for row in split_totals.values()),
            "eligible_rate": all_counts.eligible / all_counts.decisive if all_counts.decisive else None,
            "eligible_rate_ci_low": low,
            "eligible_rate_ci_high": high,
            "noise_precision": all_counts.ineligible / all_counts.decisive if all_counts.decisive else None,
            "eligible_loss_share": all_counts.eligible / total_eligible if total_eligible else None,
            "retained_eligible_recall": 1 - all_counts.eligible / total_eligible if total_eligible else None,
            "ineligible_odds_ratio": odds_ratio(all_counts.eligible, all_counts.ineligible, total_eligible, total_ineligible),
            "mutual_information_bits": mutual_information_binary(
                all_counts.eligible, all_counts.ineligible, total_eligible, total_ineligible
            ),
        })
        monthly_rates = [
            counts.eligible / counts.decisive
            for counts in by_feature_month.get(feature, {}).values()
            if counts.decisive >= 20
        ]
        report["worst_month_eligible_rate_min_support_20"] = max(monthly_rates) if monthly_rates else None
        reports.append(report)
    reports.sort(key=lambda row: (-int(row["support"]), str(row["feature"])))
    return reports


def candidate_grade(row: Mapping[str, Any]) -> str:
    supports = [int(row.get(f"{prefix}_support") or 0) for prefix in ("discovery", "validation", "final")]
    rates = [row.get(f"{prefix}_eligible_rate") for prefix in ("discovery", "validation", "final")]
    if min(supports) < 30 or any(rate is None for rate in rates):
        return "insufficient_forward_support"
    worst = max(float(rate) for rate in rates if rate is not None)
    if worst <= 0.01:
        return "high_precision_candidate"
    if worst <= 0.025:
        return "promising_candidate"
    if worst <= 0.05:
        return "audit_candidate"
    return "not_safe_for_rejection"


def select_candidates(report_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_prefixes = (
        "rule:", "metadata_signature=", "tag=", "channel=", "channel_set=",
        "tag_set=", "tag_channel=", "channel_pair=",
    )
    candidates: list[dict[str, Any]] = []
    for row in report_rows:
        feature = str(row["feature"])
        if not feature.startswith(allowed_prefixes):
            continue
        if int(row.get("discovery_support") or 0) < 100:
            continue
        grade = candidate_grade(row)
        if grade == "not_safe_for_rejection":
            continue
        candidates.append({**row, "candidate_grade": grade})
    candidates.sort(
        key=lambda row: (
            {"high_precision_candidate": 0, "promising_candidate": 1, "audit_candidate": 2, "insufficient_forward_support": 3}[row["candidate_grade"]],
            -int(row["discovery_support"]),
            str(row["feature"]),
        )
    )
    return candidates


def rule_waterfall(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_rules: int = 100,
) -> list[dict[str, Any]]:
    chosen = [
        str(row["feature"])
        for row in candidates
        if row["candidate_grade"] in {"high_precision_candidate", "promising_candidate"}
    ][:max_rules]
    chosen_set = set(chosen)
    first_match_counts: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    unmatched: dict[str, Counts] = defaultdict(Counts)
    order = {feature: index for index, feature in enumerate(chosen)}
    for row in rows:
        matches = chosen_set.intersection(feature_names(row))
        split = str(row["split"])
        if not matches:
            unmatched[split].add(str(row["label"]))
            continue
        first = min(matches, key=order.__getitem__)
        first_match_counts[first][split].add(str(row["label"]))
    totals = {split: Counts() for split in SPLITS}
    for row in rows:
        totals[str(row["split"])].add(str(row["label"]))
    cumulative = {split: Counts() for split in SPLITS}
    result: list[dict[str, Any]] = []
    for index, feature in enumerate(chosen, 1):
        record: dict[str, Any] = {"order": index, "feature": feature}
        for split in SPLITS:
            marginal = first_match_counts[feature][split]
            aggregate = cumulative[split]
            aggregate.total += marginal.total
            aggregate.eligible += marginal.eligible
            aggregate.ineligible += marginal.ineligible
            aggregate.insufficient += marginal.insufficient
            prefix = split.replace("_2026_jan_apr", "").replace("_2026_may_aug", "").replace("_2025", "")
            record[f"{prefix}_marginal_rejected"] = marginal.total
            record[f"{prefix}_marginal_eligible"] = marginal.eligible
            record[f"{prefix}_cumulative_rejected"] = aggregate.total
            record[f"{prefix}_cumulative_compute_reduction"] = aggregate.total / totals[split].total if totals[split].total else 0.0
            record[f"{prefix}_cumulative_retained_eligible_recall"] = 1 - aggregate.eligible / totals[split].eligible if totals[split].eligible else 1.0
        result.append(record)
    return result


def write_csv_new(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write("")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_article_features_new(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    keep = (
        "source_id", "label", "authority_class", "certification_level", "human_certified",
        "source_dataset", "published_at_text", "published_month", "split", "provider", "tickers",
        "ticker_count", "provider_tags", "channels", "content_quality_flags", "update_delay_seconds",
        "session_segment", "session_date", "hour_et", "weekday_et", "any_ticker_first_session",
        "all_tickers_first_session", "min_ticker_session_ordinal", "max_ticker_session_ordinal",
        "min_seconds_since_previous_ticker_news", "max_seconds_since_previous_ticker_news",
        "any_ticker_news_within_5m", "any_ticker_news_within_30m", "rendered_chars",
        "rendered_text_hash", *TEXT_PATTERNS.keys(), "question_title", "title_only",
    )
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json({key: row.get(key) for key in keep}))
            handle.write("\n")


def _top_features(
    reports: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
    limit: int,
    minimum_support: int = 100,
) -> list[dict[str, Any]]:
    rows = [
        row for row in reports
        if str(row["feature"]).startswith(prefix) and int(row["support"]) >= minimum_support
    ]
    rows.sort(key=lambda row: (float(row.get("eligible_rate") or 0), -int(row["support"]), str(row["feature"])))
    return [dict(row) for row in rows[:limit]]


def build_report(
    *,
    input_paths: InputPaths,
    verification: Mapping[str, Any],
    label_counts: Mapping[str, int],
    load_summary: Mapping[str, Any],
    history_summary: Mapping[str, Any],
    text_summary: Mapping[str, Any],
    split_totals: Mapping[str, Counts],
    feature_reports: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    waterfall: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "2025 through August 2026 current forecast-label authority",
        "inputs": {
            "labels": str(input_paths.labels),
            "metadata": str(input_paths.metadata),
            "rendered_texts": str(input_paths.rendered_texts),
            "verification": verification,
        },
        "current_authority_label_counts": dict(label_counts),
        "population": {
            **load_summary,
            "splits": {
                split: {
                    "total": counts.total,
                    "eligible": counts.eligible,
                    "ineligible": counts.ineligible,
                    "insufficient": counts.insufficient,
                }
                for split, counts in split_totals.items()
            },
        },
        "causal_ticker_history": history_summary,
        "rendered_text": text_summary,
        "feature_count": len(feature_reports),
        "candidate_count": len(candidates),
        "candidate_grades": dict(Counter(str(row["candidate_grade"]) for row in candidates)),
        "top_low_eligible_channels": _top_features(feature_reports, prefix="channel=", limit=20),
        "top_low_eligible_tags": _top_features(feature_reports, prefix="tag=", limit=20),
        "rule_features": [dict(row) for row in feature_reports if str(row["feature"]) in RULE_FEATURES],
        "ticker_history_features": [
            dict(row) for row in feature_reports
            if str(row["feature"]).startswith(("any_ticker_first_session=", "all_tickers_first_session=", "min_seconds_since_previous="))
        ],
        "top_candidates": [dict(row) for row in candidates[:100]],
        "waterfall": list(waterfall),
        "limitations": [
            "Current labels include provisional single-pass authority and are not assumed error-free.",
            "Metadata last-updated time is analyzed as a provider-row update proxy, not proven live available_at time.",
            "Ticker history uses strictly earlier publication timestamps with source_id tie-breaking; operational replay should use true available_at ordering when present.",
            "Candidate rules are statistical discovery evidence and require blind exception review before production rejection.",
            "Text families use bounded deterministic patterns for analysis; they are not production News Synthesis changes.",
        ],
    }


def build_validation(
    *,
    rows: Sequence[Mapping[str, Any]],
    feature_reports: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    output_paths: Sequence[Path],
) -> dict[str, Any]:
    ids = [str(row["source_id"]) for row in rows]
    split_set = {str(row["split"]) for row in rows}
    validation = {
        "status": "passed",
        "analysis_version": ANALYSIS_VERSION,
        "article_rows": len(rows),
        "unique_source_ids": len(set(ids)),
        "source_ids_unique": len(ids) == len(set(ids)),
        "exact_splits_present": split_set == set(SPLITS),
        "feature_rows": len(feature_reports),
        "candidate_rows": len(candidates),
        "rendered_hashes_present": all(bool(row.get("rendered_text_hash")) for row in rows),
        "ticker_history_nonnegative": all(
            row.get("min_seconds_since_previous_ticker_news") is None
            or float(row["min_seconds_since_previous_ticker_news"]) >= 0
            for row in rows
        ),
        "outputs_exist": all(path.exists() for path in output_paths),
    }
    if not all(
        value for key, value in validation.items()
        if key in {
            "source_ids_unique", "exact_splits_present", "rendered_hashes_present",
            "ticker_history_nonnegative", "outputs_exist",
        }
    ):
        raise ValueError(f"analysis validation failed: {validation}")
    return validation


def build_markdown(report: Mapping[str, Any]) -> str:
    split_lines = []
    for split, counts in report["population"]["splits"].items():
        split_lines.append(
            f"| {split} | {counts['total']:,} | {counts['eligible']:,} | "
            f"{counts['ineligible']:,} | {counts['insufficient']:,} |"
        )
    rule_lines = []
    for row in sorted(report["rule_features"], key=lambda item: int(item["support"]), reverse=True):
        rule_lines.append(
            f"| `{row['feature']}` | {int(row['support']):,} | "
            f"{100*float(row['eligible_rate'] or 0):.2f}% | "
            f"{100*float(row.get('final_eligible_rate') or 0):.2f}% | "
            f"{100*float(row['coverage']):.2f}% |"
        )
    candidate_lines = []
    for row in report["top_candidates"][:30]:
        candidate_lines.append(
            f"| `{row['feature']}` | {row['candidate_grade']} | {int(row['support']):,} | "
            f"{100*float(row['eligible_rate'] or 0):.2f}% | "
            f"{100*float(row.get('final_eligible_rate') or 0):.2f}% |"
        )
    waterfall = report.get("waterfall") or []
    last = waterfall[-1] if waterfall else None
    waterfall_summary = (
        f"The ordered candidate waterfall rejects {100*float(last['final_cumulative_compute_reduction']):.2f}% "
        f"of final-period articles while retaining {100*float(last['final_cumulative_retained_eligible_recall']):.2f}% "
        "of currently labeled eligible articles."
        if last else "No candidates met the forward-support and precision gates for a waterfall."
    )
    return "\n".join((
        "# News Synthesis Provider-Filter Feature Audit",
        "",
        f"Analysis version: `{report['analysis_version']}`",
        "",
        "This report evaluates deterministic provider, headline, time, and causal ticker-history features. "
        "It discovers candidate forecast-noise routing rules; it does not certify production rejection or alter News Synthesis.",
        "",
        "## Population",
        "",
        "| Split | Articles | Eligible | Ineligible | Insufficient |",
        "|---|---:|---:|---:|---:|",
        *split_lines,
        "",
        "## Predefined semantic paths",
        "",
        "| Candidate path | Support | Eligible rate | Final eligible rate | Coverage |",
        "|---|---:|---:|---:|---:|",
        *(rule_lines or ["| none | 0 | n/a | n/a | n/a |"]),
        "",
        "## Strongest forward-tested candidates",
        "",
        "| Feature | Grade | Support | Eligible rate | Final eligible rate |",
        "|---|---|---:|---:|---:|",
        *(candidate_lines or ["| none | insufficient | 0 | n/a | n/a |"]),
        "",
        "## Candidate waterfall",
        "",
        waterfall_summary,
        "",
        "## Interpretation boundary",
        "",
        "- The labels are development authority and contain known provisional/error risk.",
        "- Candidate rules require blind review of labeled-eligible exceptions before production use.",
        "- All ticker-history features use prior publication records only; true live certification still requires available-time replay.",
        "- Noise means excluded from the issuer-forecast path, not deleted or semantically worthless.",
        "",
    ))


def run_analysis(
    *,
    authority_root: Path = DEFAULT_AUTHORITY_ROOT,
    metadata_root: Path = DEFAULT_METADATA_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite analysis output: {output_root}")
    output_root.mkdir(parents=True)
    paths = _expected_input_paths(authority_root, metadata_root)
    verification = verify_inputs(paths)
    labels, label_counts = load_current_labels(paths.labels)
    rows, load_summary = load_analysis_rows(paths.metadata, labels)
    history_summary = attach_ticker_history(rows)
    flags, text_summary = load_text_flags(paths.rendered_texts, {row["source_id"] for row in rows})
    attach_text_flags(rows, flags)
    by_feature, by_feature_month, split_totals, _month_totals = aggregate_features(rows)
    feature_reports = feature_report_rows(by_feature, by_feature_month, split_totals)
    candidates = select_candidates(feature_reports)
    waterfall = rule_waterfall(rows, candidates)

    article_path = output_root / "ARTICLE_FEATURES.jsonl"
    feature_path = output_root / "FEATURE_STRENGTH.csv"
    candidate_path = output_root / "CANDIDATE_RULES.csv"
    waterfall_path = output_root / "RULE_WATERFALL.csv"
    write_article_features_new(article_path, rows)
    write_csv_new(feature_path, feature_reports)
    write_csv_new(candidate_path, candidates)
    write_csv_new(waterfall_path, waterfall)

    report = build_report(
        input_paths=paths,
        verification=verification,
        label_counts=label_counts,
        load_summary=load_summary,
        history_summary=history_summary,
        text_summary=text_summary,
        split_totals=split_totals,
        feature_reports=feature_reports,
        candidates=candidates,
        waterfall=waterfall,
    )
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    write_json_new(report_path, report)
    with report_md_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(build_markdown(report))

    validation_path = output_root / "VALIDATION.json"
    output_paths = (article_path, feature_path, candidate_path, waterfall_path, report_path, report_md_path)
    validation = build_validation(
        rows=rows,
        feature_reports=feature_reports,
        candidates=candidates,
        output_paths=output_paths,
    )
    write_json_new(validation_path, validation)
    hash_manifest = {
        "analysis_version": ANALYSIS_VERSION,
        "inputs": report["inputs"],
        "outputs": {
            path.name: {"sha256": sha256_path(path), "bytes": path.stat().st_size}
            for path in (*output_paths, validation_path)
        },
    }
    write_json_new(output_root / "HASH_MANIFEST.json", hash_manifest)
    return report
