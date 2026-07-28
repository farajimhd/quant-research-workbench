from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_labeling.gpt_oss_v1.compare import AGREEMENT_FIELDS
from research.news_labeling.gpt_oss_v1.data import read_jsonl


DEFAULT_SAMPLE = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
    r"\news_labeling\gpt_oss_v1\shared\sample.jsonl"
)
DEFAULT_OPENAI_ROOT = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
    r"\news_labeling\openai_batch_v1\models"
)
DEFAULT_OSS_ROOT = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
    r"\news_labeling\gpt_oss_v1\models"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\news_labeling\market_reaction_evaluation_v1"
)
# V3 is the repaired ordinal-preserving authority, but its current build is
# partial. V2 plus its complete quality overlay is the only authority that
# covers the frozen historical sample. The report exposes this limitation.
DEFAULT_REACTION_TABLE = "q_live.news_reaction_labels_v2"
SOL_MODEL = "gpt-5.6-sol"

# These are the already-established flat-span widths used by the reaction
# experiments. They prevent sub-tick/noise movement from becoming a direction.
MINIMUM_SPAN_PCT = {
    "1m": 0.10,
    "5m": 0.20,
    "10m": 0.20,
    "30m": 0.50,
    "1h": 0.50,
    "2h": 1.00,
    "3h": 1.00,
    "premarket_close": 1.00,
    "regular_close": 1.00,
    "extended_close": 1.00,
}
CLASS_ORDER = ("negative", "neutral", "positive")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare frozen semantic labels to Sol and evaluate article "
            "sentiment against exact-event market reactions."
        )
    )
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--openai-root", type=Path, default=DEFAULT_OPENAI_ROOT)
    parser.add_argument("--oss-root", type=Path, default=DEFAULT_OSS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reaction-table", default=DEFAULT_REACTION_TABLE)
    parser.add_argument("--sol-model", default=SOL_MODEL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    load_env_files(discover_env_files(repo_root), verbose=True)
    sample = read_jsonl(args.sample)
    if len(sample) != 192:
        raise RuntimeError(f"Expected frozen 192-row sample, found {len(sample)}")
    sample_by_id = {str(row["canonical_news_id"]): row for row in sample}
    models = load_models(args.openai_root, args.oss_root, sample_by_id)
    if args.sol_model not in models:
        raise RuntimeError(f"Sol reference output is missing: {args.sol_model}")

    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=180,
    )
    try:
        reactions = fetch_reactions(
            client, args.reaction_table, list(sample_by_id), sample_by_id
        )
    finally:
        client.close()

    payload = evaluate(sample_by_id, models, reactions, args.sol_model)
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "evaluation.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_market_csv(args.output_root / "market_alignment.csv", payload)
    report = args.output_root / "MARKET_REACTION_EVALUATION.md"
    report.write_text(render_markdown(payload), encoding="utf-8")
    print(f"COMPLETED report={report}", flush=True)
    return 0


def load_models(
    openai_root: Path,
    oss_root: Path,
    sample_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    roots: list[Path] = []
    for parent in (openai_root, oss_root):
        if parent.exists():
            roots.extend(
                child
                for child in sorted(parent.iterdir())
                if child.is_dir() and (child / "labels.jsonl").exists()
            )
    models: dict[str, dict[str, dict[str, Any]]] = {}
    for root in roots:
        rows = {
            str(row["canonical_news_id"]): row
            for row in read_jsonl(root / "labels.jsonl")
            if row.get("status") == "completed"
        }
        unknown = set(rows) - set(sample_by_id)
        if unknown:
            raise RuntimeError(f"{root} has {len(unknown)} unknown sample identities")
        drift = [
            identifier
            for identifier, row in rows.items()
            if row.get("text_sha256")
            != sample_by_id[identifier].get("text_sha256")
        ]
        if drift:
            raise RuntimeError(f"{root} has {len(drift)} rendered-text drift rows")
        models[root.name] = rows
    return models


def fetch_reactions(
    client: ClickHouseHttpClient,
    table: str,
    identities: list[str],
    sample_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    database, table_name = split_table(table)
    is_v2 = table_name == "news_reaction_labels_v2"
    source = f"(SELECT * FROM `{database}`.`{table_name}` FINAL) AS l"
    overlay_join = ""
    overlay_filter = ""
    corporate_action_expr = "l.corporate_action_overlap"
    if is_v2:
        overlay_join = """
        INNER JOIN
        (
            SELECT *
            FROM `q_live`.`news_reaction_quality_overlay_v1` FINAL
        ) AS o
          ON o.canonical_news_id = l.canonical_news_id
         AND o.ticker = l.ticker
         AND o.published_at_utc = l.published_at_utc
         AND o.horizon_code = l.horizon_code
        """
        overlay_filter = "AND o.eligible_for_statistics = 1"
        corporate_action_expr = "o.corporate_action_overlap"
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(identities), 80):
        quoted = ",".join(sql_string(value) for value in identities[offset : offset + 80])
        sql = f"""
        SELECT l.canonical_news_id, l.ticker,
               toString(l.published_at_utc) AS published_at_utc,
               l.horizon_code, l.anchor_price, l.target_return,
               l.high_return, l.low_return,
               l.abnormal_target_return, l.abnormal_high_return,
               l.abnormal_low_return, l.observation_count,
               l.overlapping_news_count,
               {corporate_action_expr} AS corporate_action_overlap,
               l.quality_status, l.quality_flags
        FROM {source}
        {overlay_join}
        WHERE l.canonical_news_id IN ({quoted})
          AND l.applicable = 1
          AND l.quality_status = 'clean'
          {overlay_filter}
          AND isNotNull(l.anchor_price)
          AND isNotNull(l.target_return)
          AND isNotNull(l.high_return)
          AND isNotNull(l.low_return)
          AND isFinite(l.target_return)
          AND isFinite(l.high_return)
          AND isFinite(l.low_return)
        FORMAT JSONEachRow
        """
        for line in client.execute(sql).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            article = sample_by_id.get(str(row["canonical_news_id"]))
            if article is None:
                continue
            if normalize_timestamp(row["published_at_utc"]) != normalize_timestamp(
                article["published_at_utc"]
            ):
                raise RuntimeError(
                    "Reaction identity timestamp mismatch for "
                    f"{row['canonical_news_id']}"
                )
            tickers = {str(value).upper() for value in article.get("tickers") or []}
            ticker = str(row["ticker"]).upper()
            if ticker not in tickers:
                continue
            row["ticker"] = ticker
            row["scope"] = (
                "single_ticker" if len(tickers) == 1 else "multi_ticker"
            )
            rows.append(row)
    return rows


def evaluate(
    sample: dict[str, dict[str, Any]],
    models: dict[str, dict[str, dict[str, Any]]],
    reactions: list[dict[str, Any]],
    sol_model: str,
) -> dict[str, Any]:
    sol = models[sol_model]
    reference = {
        name: semantic_agreement(sol, rows)
        for name, rows in models.items()
        if name != sol_model
    }
    reaction_ids = {str(row["canonical_news_id"]) for row in reactions}
    coverage = {
        "sample_articles": len(sample),
        "sample_no_ticker": sum(not row.get("tickers") for row in sample.values()),
        "sample_single_ticker": sum(
            len(row.get("tickers") or []) == 1 for row in sample.values()
        ),
        "sample_multi_ticker": sum(
            len(row.get("tickers") or []) > 1 for row in sample.values()
        ),
        "clean_reaction_articles": len(reaction_ids),
        "clean_reaction_links": len(
            {
                (row["canonical_news_id"], row["ticker"])
                for row in reactions
            }
        ),
        "clean_reaction_rows": len(reactions),
    }
    market: dict[str, Any] = {}
    for name, rows in models.items():
        model_result: dict[str, Any] = {}
        for scope in ("single_ticker", "multi_ticker", "all"):
            scoped = [
                reaction
                for reaction in reactions
                if str(reaction["canonical_news_id"]) in rows
                and (scope == "all" or reaction["scope"] == scope)
            ]
            model_result[scope] = market_metrics(rows, scoped)
        market[name] = model_result
    matched_market: dict[str, Any] = {}
    for name, rows in models.items():
        if name == sol_model:
            continue
        common_ids = set(sol) & set(rows)
        common_reactions = [
            row
            for row in reactions
            if row["scope"] == "single_ticker"
            and str(row["canonical_news_id"]) in common_ids
        ]
        matched_market[name] = {
            "common_articles": len(common_ids),
            "candidate": market_metrics(rows, common_reactions),
            "sol": market_metrics(sol, common_reactions),
        }
    return {
        "contract": {
            "semantic_reference": sol_model,
            "semantic_reference_note": (
                "Sol is treated as a reference labeler, not independently "
                "verified human ground truth."
            ),
            "market_authority": (
                "q_live.news_reaction_labels_v2 clean/applicable exact-event "
                "trade returns joined to news_reaction_quality_overlay_v1. "
                "The repaired ordinal-preserving V3 authority is still partial. "
                "Quotes are not merged with transaction prices."
            ),
            "actual_class": (
                "neutral when upside plus downside excursion is below the "
                "horizon flat-span threshold; otherwise the larger absolute "
                "excursion determines positive versus negative."
            ),
            "prediction_class": (
                "positive and negative map directly; neutral and mixed both "
                "map to neutral."
            ),
            "minimum_span_pct": MINIMUM_SPAN_PCT,
        },
        "coverage": coverage,
        "models": {
            name: {"completed_articles": len(rows)}
            for name, rows in models.items()
        },
        "sol_reference_agreement": reference,
        "market_alignment": market,
        "matched_market_alignment": matched_market,
    }


def semantic_agreement(
    sol: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    common = sorted(set(sol) & set(candidate))
    if not common:
        return {"common_articles": 0}
    fields = {
        name: sum(
            nested_value(sol[item]["label"], path)
            == nested_value(candidate[item]["label"], path)
            for item in common
        )
        / len(common)
        for name, path in AGREEMENT_FIELDS.items()
    }
    exact = 0
    jaccards: list[float] = []
    for identifier in common:
        first = event_set(sol[identifier]["label"])
        second = event_set(candidate[identifier]["label"])
        union = first | second
        exact += first == second
        jaccards.append(len(first & second) / len(union) if union else 1.0)
    return {
        "common_articles": len(common),
        "field_agreement": fields,
        "mean_field_agreement": statistics.fmean(fields.values()),
        "event_exact_match": exact / len(common),
        "event_mean_jaccard": statistics.fmean(jaccards),
    }


def market_metrics(
    labels: dict[str, dict[str, Any]],
    reactions: list[dict[str, Any]],
) -> dict[str, Any]:
    by_horizon: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for row in reactions:
        horizon = str(row["horizon_code"])
        if horizon not in MINIMUM_SPAN_PCT:
            continue
        identifier = str(row["canonical_news_id"])
        prediction = prediction_class(labels[identifier]["label"])
        actual = reaction_class(
            float(row["high_return"]),
            float(row["low_return"]),
            MINIMUM_SPAN_PCT[horizon],
        )
        confidence = float(
            labels[identifier]["label"].get("sentiment", {}).get("confidence")
            or 0.0
        )
        by_horizon[horizon].append((prediction, actual, confidence))
    return {
        horizon: classification_metrics(rows)
        for horizon, rows in sorted(by_horizon.items(), key=horizon_key)
    }


def reaction_class(
    high_return: float, low_return: float, minimum_span_pct: float
) -> str:
    upside = max(0.0, high_return * 100.0)
    downside = max(0.0, -low_return * 100.0)
    if upside + downside <= minimum_span_pct:
        return "neutral"
    return "positive" if upside > downside else "negative"


def prediction_class(label: dict[str, Any]) -> str:
    value = str(label.get("sentiment", {}).get("overall") or "neutral")
    return value if value in {"positive", "negative"} else "neutral"


def classification_metrics(
    rows: list[tuple[str, str, float]]
) -> dict[str, Any]:
    confusion = {
        actual: {predicted: 0 for predicted in CLASS_ORDER}
        for actual in CLASS_ORDER
    }
    for predicted, actual, _confidence in rows:
        confusion[actual][predicted] += 1
    total = len(rows)
    correct = sum(confusion[value][value] for value in CLASS_ORDER)
    recalls: list[float] = []
    precisions: list[float] = []
    f1s: list[float] = []
    for value in CLASS_ORDER:
        tp = confusion[value][value]
        actual_count = sum(confusion[value].values())
        predicted_count = sum(confusion[actual][value] for actual in CLASS_ORDER)
        recall = tp / actual_count if actual_count else math.nan
        precision = tp / predicted_count if predicted_count else math.nan
        if math.isfinite(recall):
            recalls.append(recall)
        if math.isfinite(precision):
            precisions.append(precision)
        if (
            math.isfinite(recall)
            and math.isfinite(precision)
            and recall + precision
        ):
            f1s.append(2 * recall * precision / (recall + precision))
    active = [
        (predicted, actual)
        for predicted, actual, _ in rows
        if predicted != "neutral" and actual != "neutral"
    ]
    return {
        "rows": total,
        "accuracy": correct / total if total else 0.0,
        "balanced_accuracy": statistics.fmean(recalls) if recalls else 0.0,
        "macro_precision": statistics.fmean(precisions) if precisions else 0.0,
        "macro_f1": statistics.fmean(f1s) if f1s else 0.0,
        "active_direction_rows": len(active),
        "active_direction_accuracy": (
            sum(predicted == actual for predicted, actual in active) / len(active)
            if active
            else 0.0
        ),
        "prediction_distribution": dict(
            Counter(predicted for predicted, _actual, _confidence in rows)
        ),
        "actual_distribution": dict(
            Counter(actual for _predicted, actual, _confidence in rows)
        ),
        "confusion": confusion,
    }


def write_market_csv(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "model",
                "scope",
                "horizon",
                "rows",
                "accuracy",
                "balanced_accuracy",
                "macro_f1",
                "active_direction_rows",
                "active_direction_accuracy",
            ]
        )
        for model, scopes in payload["market_alignment"].items():
            for scope, horizons in scopes.items():
                for horizon, row in horizons.items():
                    writer.writerow(
                        [
                            model,
                            scope,
                            horizon,
                            row["rows"],
                            row["accuracy"],
                            row["balanced_accuracy"],
                            row["macro_f1"],
                            row["active_direction_rows"],
                            row["active_direction_accuracy"],
                        ]
                    )


def render_markdown(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    lines = [
        "# Frozen news labels versus exact market reactions",
        "",
        "## Interpretation boundary",
        "",
        "Sol is treated as the semantic reference requested for this experiment. "
        "Market price response does **not** prove that a semantic label is true "
        "or false; it measures whether article-level sentiment aligned with the "
        "subsequent transaction-price path.",
        "",
        "The complete historical reaction authority used here is deliberately "
        "trade-only and is filtered through its corporate-action/outlier quality "
        "overlay. The repaired ordinal-preserving V3 authority is still partial. "
        "Quotes remain useful for spread and liquidity validation, but bid/ask "
        "quotes are not mixed into the transaction-return target.",
        "",
        "## Coverage",
        "",
        f"- Frozen articles: **{coverage['sample_articles']:,}**",
        f"- No ticker / single ticker / multi ticker: "
        f"**{coverage['sample_no_ticker']:,} / "
        f"{coverage['sample_single_ticker']:,} / "
        f"{coverage['sample_multi_ticker']:,}**",
        f"- Articles with clean exact-event reactions: "
        f"**{coverage['clean_reaction_articles']:,}**",
        f"- Clean article-ticker links: **{coverage['clean_reaction_links']:,}**",
        "",
        "## Agreement with Sol",
        "",
        "| Model | Completed | Common with Sol | Sentiment | Mean fields | Event exact | Event Jaccard |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model, agreement in payload["sol_reference_agreement"].items():
        completed = payload["models"][model]["completed_articles"]
        fields = agreement.get("field_agreement", {})
        lines.append(
            f"| {model} | {completed:,} | "
            f"{agreement.get('common_articles', 0):,} | "
            f"{fields.get('sentiment.overall', 0.0):.1%} | "
            f"{agreement.get('mean_field_agreement', 0.0):.1%} | "
            f"{agreement.get('event_exact_match', 0.0):.1%} | "
            f"{agreement.get('event_mean_jaccard', 0.0):.1%} |"
        )
    lines.extend(
        (
            "",
            "## Market alignment on single-ticker articles",
            "",
            "Three-class accuracy maps positive/negative directly and maps neutral "
            "or mixed language to neutral. Actual direction is the dominant "
            "post-news excursion after the horizon-specific noise threshold.",
            "",
            "| Model | Horizon | Rows | Accuracy | Balanced | Macro F1 | Active direction |",
            "|---|---|---:|---:|---:|---:|---:|",
        )
    )
    for model, scopes in payload["market_alignment"].items():
        for horizon, row in scopes["single_ticker"].items():
            lines.append(
                f"| {model} | {horizon} | {row['rows']:,} | "
                f"{row['accuracy']:.1%} | {row['balanced_accuracy']:.1%} | "
                f"{row['macro_f1']:.1%} | "
                f"{row['active_direction_accuracy']:.1%} "
                f"({row['active_direction_rows']:,}) |"
            )
    lines.extend(
        (
            "",
            "## Fair market comparison on the same articles",
            "",
            "Each row compares a candidate and Sol on the exact same "
            "single-ticker article/horizon links.",
            "",
            "| Model | Horizon | Rows | Candidate accuracy | Sol accuracy | "
            "Candidate active direction | Sol active direction |",
            "|---|---|---:|---:|---:|---:|---:|",
        )
    )
    for model, matched in payload["matched_market_alignment"].items():
        common_horizons = sorted(
            set(matched["candidate"]) & set(matched["sol"]),
            key=lambda value: horizon_key((value, None)),
        )
        for horizon in common_horizons:
            candidate = matched["candidate"][horizon]
            sol_row = matched["sol"][horizon]
            lines.append(
                f"| {model} | {horizon} | {candidate['rows']:,} | "
                f"{candidate['accuracy']:.1%} | {sol_row['accuracy']:.1%} | "
                f"{candidate['active_direction_accuracy']:.1%} | "
                f"{sol_row['active_direction_accuracy']:.1%} |"
            )
    lines.extend(
        (
            "",
            "## Sol confusion by horizon",
            "",
        )
    )
    sol = payload["contract"]["semantic_reference"]
    for horizon, row in payload["market_alignment"][sol]["single_ticker"].items():
        lines.extend(
            (
                f"### {horizon}",
                "",
                confusion_markdown(row["confusion"]),
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def confusion_markdown(confusion: dict[str, dict[str, int]]) -> str:
    lines = [
        "| Actual \\ Predicted | Negative | Neutral | Positive |",
        "|---|---:|---:|---:|",
    ]
    for actual in CLASS_ORDER:
        row = confusion[actual]
        lines.append(
            f"| {actual} | {row['negative']:,} | "
            f"{row['neutral']:,} | {row['positive']:,} |"
        )
    return "\n".join(lines)


def nested_value(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def event_set(label: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (
            str(row.get("family")),
            str(row.get("subtype")),
            str(row.get("direction")),
        )
        for row in label.get("events") or []
        if isinstance(row, dict)
    }


def split_table(value: str) -> tuple[str, str]:
    parts = value.split(".", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError("reaction table must be database.table")
    return parts[0], parts[1]


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def normalize_timestamp(value: str) -> datetime:
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def horizon_key(item: tuple[str, Any]) -> tuple[int, str]:
    order = {
        "1m": 0,
        "5m": 1,
        "10m": 2,
        "30m": 3,
        "1h": 4,
        "2h": 5,
        "3h": 6,
        "premarket_close": 7,
        "regular_close": 8,
        "extended_close": 9,
    }
    return order.get(item[0], 99), item[0]


if __name__ == "__main__":
    raise SystemExit(main())
