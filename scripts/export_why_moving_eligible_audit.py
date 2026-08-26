from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "services" / "text-intelligence"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import iter_jsonl
from text_intelligence.config import IntelligenceConfig


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
NEWS_ROOT = RUNTIME_ROOT / "news_synthesis_v1"
FEATURES = (
    NEWS_ROOT
    / "provider_filter_feature_audit_v6_provider_path_exceptions_final"
    / "ARTICLE_FEATURES.jsonl"
)
TRAINING_AUTHORITY = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1"
    / "article_forecast_eligibility_labels.jsonl"
)
HOLDOUT_ROOT = NEWS_ROOT / "forecast_eligibility_august_2026_temporal_holdout_v1"
DEFAULT_OUTPUT = NEWS_ROOT / "why_moving_eligible_audit_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _training_rows() -> list[dict[str, Any]]:
    features = {
        str(row["source_id"]): row
        for row in iter_jsonl(FEATURES)
        if bool(row.get("why_moving"))
    }
    labels = {}
    for row in iter_jsonl(TRAINING_AUTHORITY):
        source_id = str(row["source_id"])
        if source_id in features:
            labels[source_id] = row
    if set(labels) != set(features):
        raise ValueError("training authority does not cover every why_moving feature row")
    result = []
    for source_id, feature in features.items():
        authority = labels[source_id]
        if str(authority["forecast_eligibility_label"]) != "eligible":
            continue
        result.append({
            **feature,
            "audit_partition": "training_2025_2026",
            "label_authority": str(authority.get("authority_class") or ""),
            "certification_level": str(authority.get("certification_level") or ""),
            "label_contract": str(authority.get("label_contract") or ""),
        })
    return result


def _holdout_rows() -> list[dict[str, Any]]:
    labels = {
        str(row["source_id"]): row
        for row in iter_jsonl(HOLDOUT_ROOT / "FINAL_LABELS_V2.jsonl")
    }
    result = []
    for row in iter_jsonl(HOLDOUT_ROOT / "SOURCE_ROWS.jsonl"):
        source_id = str(row["source_id"])
        label = labels.get(source_id)
        if (
            bool(row.get("why_moving"))
            and label is not None
            and str(label.get("final_label")) == "eligible"
        ):
            result.append({
                **row,
                "audit_partition": "holdout_august_2026",
                "label_authority": str(label.get("decision_path") or ""),
                "certification_level": "holdout_final_label_v2",
                "label_contract": "forecast_eligibility_august_2026_temporal_holdout_v1",
            })
    return result


def _titles(client: ClickHouseHttpClient, source_ids: Iterable[str]) -> dict[str, str]:
    identifiers = sorted(set(source_ids))
    result: dict[str, str] = {}
    for start in range(0, len(identifiers), 500):
        batch = identifiers[start:start + 500]
        values = ",".join(sql_string(value) for value in batch)
        for row in client.iter_json_each_row(f"""
SELECT canonical_news_id,title
FROM q_live.benzinga_news_event_v2 FINAL
WHERE canonical_news_id IN ({values})
FORMAT JSONEachRow
"""):
            source_id = str(row["canonical_news_id"])
            title = str(row.get("title") or "").strip()
            if source_id in result and result[source_id] != title:
                raise ValueError(f"multiple titles for canonical News ID: {source_id}")
            result[source_id] = title
    missing = set(identifiers) - set(result)
    if missing:
        preview = ", ".join(sorted(missing)[:10])
        raise ValueError(f"canonical titles are unavailable for {len(missing)} rows: {preview}")
    if any(not value for value in result.values()):
        raise ValueError("one or more canonical titles are empty")
    return result


def _escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("|", "\\|")
        .strip()
    )


def _list(values: Any, limit: int = 12) -> str:
    items = [str(value).strip() for value in values or () if str(value).strip()]
    visible = items[:limit]
    rendered = ", ".join(visible)
    if len(items) > limit:
        rendered += f", +{len(items) - limit} more"
    return rendered or "none"


def _recency(value: Any) -> str:
    if value in (None, ""):
        return "none"
    seconds = float(value)
    if seconds < 60:
        return f"{seconds:g}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _metadata(row: dict[str, Any]) -> str:
    values = [
        f"source_id={row['source_id']}",
        f"published={row.get('published_at_text') or row.get('published_at_utc')}",
        f"split={row.get('split')}",
        f"provider={row.get('provider')}",
        f"tickers={_list(row.get('tickers'))}",
        f"ticker_count={row.get('ticker_count')}",
        f"session={row.get('session_segment')}",
        f"material_event={str(bool(row.get('material_event'))).lower()}",
        f"prior_news_min={_recency(row.get('min_seconds_since_previous_ticker_news'))}",
        f"session_ordinal={row.get('min_ticker_session_ordinal')}-{row.get('max_ticker_session_ordinal')}",
        f"channels={_list(row.get('channels'))}",
        f"tags={_list(row.get('provider_tags'))}",
        f"authority={row.get('label_authority')}",
        f"certification={row.get('certification_level')}",
    ]
    return "<br>".join(_escape(value) for value in values)


def _render(rows: list[dict[str, Any]]) -> str:
    counts = Counter(str(row["audit_partition"]) for row in rows)
    lines = [
        "# Why-moving articles currently labeled forecast-eligible",
        "",
        "This is a manual-review queue, not a relabeling decision. It contains every `why_moving` article labeled `eligible` in the DeepFM training authority and August 2026 holdout authority.",
        "",
        f"- Total: **{len(rows):,}**",
        f"- Training 2025–2026: **{counts['training_2025_2026']:,}**",
        f"- August 2026 holdout: **{counts['holdout_august_2026']:,}**",
        "- Suggested review entry: replace `pending` with `eligible`, `ineligible`, or `needs context`, then add a short reason.",
        "",
        "| # | Partition | Title | News metadata | Review decision | Review reason |",
        "|---:|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"| {index} | {_escape(row['audit_partition'])} | {_escape(row['title'])} | "
            f"{_metadata(row)} | pending |  |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export eligible why_moving supervision for manual review")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output_root.exists():
        raise FileExistsError(args.output_root)

    rows = _training_rows() + _holdout_rows()
    source_ids = [str(row["source_id"]) for row in rows]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("training and holdout audit populations overlap")

    IntelligenceConfig.from_env()
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), timeout_seconds=60,
    )
    try:
        titles = _titles(client, source_ids)
    finally:
        client.close()
    for row in rows:
        row["title"] = titles[str(row["source_id"])]
    rows.sort(key=lambda row: (
        str(row.get("published_at_text") or row.get("published_at_utc") or ""),
        str(row["source_id"]),
    ))

    args.output_root.mkdir(parents=True)
    markdown_path = args.output_root / "WHY_MOVING_ELIGIBLE_AUDIT.md"
    markdown_path.write_text(_render(rows), encoding="utf-8", newline="\n")
    manifest = {
        "status": "complete",
        "rows": len(rows),
        "partitions": dict(Counter(str(row["audit_partition"]) for row in rows)),
        "output": {"path": str(markdown_path), "sha256": _sha256(markdown_path)},
        "inputs": {
            "features": {"path": str(FEATURES), "sha256": _sha256(FEATURES)},
            "training_authority": {"path": str(TRAINING_AUTHORITY), "sha256": _sha256(TRAINING_AUTHORITY)},
            "holdout_sources": {"path": str(HOLDOUT_ROOT / 'SOURCE_ROWS.jsonl'), "sha256": _sha256(HOLDOUT_ROOT / 'SOURCE_ROWS.jsonl')},
            "holdout_labels": {"path": str(HOLDOUT_ROOT / 'FINAL_LABELS_V2.jsonl'), "sha256": _sha256(HOLDOUT_ROOT / 'FINAL_LABELS_V2.jsonl')},
        },
    }
    manifest_path = args.output_root / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
