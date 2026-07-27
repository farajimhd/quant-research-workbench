from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .data import read_jsonl


AGREEMENT_FIELDS = {
    "source.origin": ("source", "origin"),
    "source.role": ("source", "role"),
    "source.issuer_relationship": ("source", "issuer_relationship"),
    "source.company_announcement": ("source", "company_announcement"),
    "sentiment.overall": ("sentiment", "overall"),
    "novelty.class": ("novelty", "class"),
    "novelty.impact_horizon": ("novelty", "impact_horizon"),
}


def compare_runs(
    *,
    sample_path: Path,
    first_root: Path,
    second_root: Path,
    output_root: Path,
    answer_key_path: Path | None,
    disagreement_limit: int,
) -> Path:
    sample = read_jsonl(sample_path)
    if not sample:
        raise RuntimeError(f"Frozen comparison sample is empty or missing: {sample_path}")
    first = _completed_by_id(first_root / "labels.jsonl")
    second = _completed_by_id(second_root / "labels.jsonl")
    sample_by_id = {str(row["canonical_news_id"]): row for row in sample}
    _validate_population(sample_by_id, first, first_root.name)
    _validate_population(sample_by_id, second, second_root.name)
    common = sorted(set(first) & set(second))
    if not common:
        raise RuntimeError("The two model runs have no completed articles in common.")

    agreement = _agreement(first, second, common)
    speed = {
        first_root.name: _speed(first.values(), first_root / "manifest.json"),
        second_root.name: _speed(second.values(), second_root / "manifest.json"),
    }
    answer_key = _answer_key(answer_key_path)
    accuracy = {
        first_root.name: _accuracy(first, answer_key),
        second_root.name: _accuracy(second, answer_key),
    } if answer_key else {}
    disagreements = _rank_disagreements(first, second, common)

    output_root.mkdir(parents=True, exist_ok=True)
    disagreements_root = output_root / "disagreements"
    disagreements_root.mkdir(exist_ok=True)
    for rank, row in enumerate(disagreements[:max(0, disagreement_limit)], start=1):
        article = sample_by_id[row["canonical_news_id"]]
        path = disagreements_root / f"{rank:03d}-{row['canonical_news_id']}.md"
        path.write_text(
            _disagreement_markdown(article, first[row["canonical_news_id"]], second[row["canonical_news_id"]]),
            encoding="utf-8",
        )
        row["path"] = str(path.relative_to(output_root)).replace("\\", "/")

    payload = {
        "sample_rows": len(sample),
        "common_completed_rows": len(common),
        "first_root": str(first_root),
        "second_root": str(second_root),
        "speed": speed,
        "agreement": agreement,
        "accuracy": accuracy,
        "answer_key_path": str(answer_key_path) if answer_key_path else None,
        "disagreements": disagreements,
    }
    (output_root / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = output_root / "COMPARISON.md"
    report.write_text(_comparison_markdown(payload), encoding="utf-8")
    return report


def _completed_by_id(path: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["canonical_news_id"]): row
        for row in read_jsonl(path)
        if row.get("status") == "completed"
    }


def _validate_population(
    sample: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any]],
    name: str,
) -> None:
    unknown = sorted(set(results) - set(sample))
    if unknown:
        raise RuntimeError(f"{name} contains {len(unknown)} identities outside the frozen sample.")
    drift = [
        identifier
        for identifier, row in results.items()
        if row.get("text_sha256") != sample[identifier].get("text_sha256")
    ]
    if drift:
        raise RuntimeError(f"{name} has rendered-text drift for {len(drift)} frozen identities.")


def _value(label: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = label
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _event_set(label: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (str(row.get("family")), str(row.get("subtype")), str(row.get("direction")))
        for row in label.get("events", [])
        if isinstance(row, dict)
    }


def _agreement(
    first: dict[str, dict[str, Any]],
    second: dict[str, dict[str, Any]],
    identities: list[str],
) -> dict[str, Any]:
    fields: dict[str, float] = {}
    for name, path in AGREEMENT_FIELDS.items():
        matches = sum(
            _value(first[item]["label"], path) == _value(second[item]["label"], path)
            for item in identities
        )
        fields[name] = matches / len(identities)
    jaccards = []
    exact = 0
    for item in identities:
        left = _event_set(first[item]["label"])
        right = _event_set(second[item]["label"])
        union = left | right
        jaccards.append(len(left & right) / len(union) if union else 1.0)
        exact += left == right
    return {
        "rows": len(identities),
        "field_accuracy": fields,
        "event_exact_match": exact / len(identities),
        "event_mean_jaccard": statistics.fmean(jaccards),
    }


def _speed(rows: Iterable[dict[str, Any]], manifest_path: Path) -> dict[str, Any]:
    materialized = list(rows)
    usage = [row.get("usage", {}) for row in materialized if isinstance(row.get("usage"), dict)]
    latencies = sorted(float(row.get("total_seconds") or 0.0) for row in usage)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "completed_rows": len(materialized),
        "failed_rows": int(manifest.get("failed_rows") or 0),
        "workers": int(manifest.get("workers") or 0),
        "wall_seconds": float(manifest.get("elapsed_seconds") or 0.0),
        "articles_per_second": float(manifest.get("articles_per_second") or 0.0),
        "latency_mean_seconds": statistics.fmean(latencies) if latencies else 0.0,
        "latency_median_seconds": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_seconds": _percentile(latencies, 0.95),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in usage),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in usage),
        "mean_completion_tokens_per_second": statistics.fmean(
            float(row.get("completion_tokens_per_second") or 0.0) for row in usage
        ) if usage else 0.0,
    }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(quantile * len(values)) - 1))
    return values[index]


def _answer_key(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    rows = read_jsonl(path)
    answer = {
        str(row["canonical_news_id"]): row["label"]
        for row in rows
        if isinstance(row.get("label"), dict)
    }
    if not answer:
        raise RuntimeError(f"Answer key has no rows with a label object: {path}")
    return answer


def _accuracy(
    predictions: dict[str, dict[str, Any]],
    answer_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    identities = sorted(set(predictions) & set(answer_key))
    if not identities:
        return {"rows": 0}
    fields = {}
    for name, path in AGREEMENT_FIELDS.items():
        fields[name] = sum(
            _value(predictions[item]["label"], path) == _value(answer_key[item], path)
            for item in identities
        ) / len(identities)
    true_positive = predicted = expected = 0
    for item in identities:
        actual = _event_set(answer_key[item])
        forecast = _event_set(predictions[item]["label"])
        true_positive += len(actual & forecast)
        predicted += len(forecast)
        expected += len(actual)
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / expected if expected else 0.0
    return {
        "rows": len(identities),
        "field_accuracy": fields,
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def _rank_disagreements(
    first: dict[str, dict[str, Any]],
    second: dict[str, dict[str, Any]],
    identities: list[str],
) -> list[dict[str, Any]]:
    ranked = []
    for item in identities:
        mismatches = [
            name
            for name, path in AGREEMENT_FIELDS.items()
            if _value(first[item]["label"], path) != _value(second[item]["label"], path)
        ]
        left = _event_set(first[item]["label"])
        right = _event_set(second[item]["label"])
        union = left | right
        jaccard = len(left & right) / len(union) if union else 1.0
        score = len(mismatches) + (1.0 - jaccard)
        if score > 0:
            ranked.append({
                "canonical_news_id": item,
                "field_mismatches": mismatches,
                "event_jaccard": jaccard,
                "score": score,
            })
    return sorted(ranked, key=lambda row: (-row["score"], row["canonical_news_id"]))


def _comparison_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# GPT-OSS 20B versus 120B news-label comparison",
        "",
        f"- Frozen sample: **{payload['sample_rows']:,}**",
        f"- Common completed rows: **{payload['common_completed_rows']:,}**",
        "",
        "## Speed",
        "",
        "| Run | Completed | Failed | Workers | Articles/s | Mean latency | P95 latency | Completion tok/s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["speed"].items():
        lines.append(
            f"| {name} | {row['completed_rows']:,} | {row['failed_rows']:,} | {row['workers']:,} | "
            f"{row['articles_per_second']:.3f} | {row['latency_mean_seconds']:.2f}s | "
            f"{row['latency_p95_seconds']:.2f}s | {row['mean_completion_tokens_per_second']:.2f} |"
        )
    lines.extend(("", "## Cross-model agreement", "", "| Field | Agreement |", "|---|---:|"))
    for name, value in payload["agreement"]["field_accuracy"].items():
        lines.append(f"| {name} | {value:.1%} |")
    lines.extend((
        f"| event exact set | {payload['agreement']['event_exact_match']:.1%} |",
        f"| event mean Jaccard | {payload['agreement']['event_mean_jaccard']:.1%} |",
        "",
        "## Semantic accuracy",
        "",
    ))
    if not payload["accuracy"]:
        lines.append(
            "No reviewed answer key was supplied. Agreement is not accuracy; inspect the disagreement "
            "packet and create a frozen answer key before choosing a semantic winner."
        )
    else:
        for name, row in payload["accuracy"].items():
            lines.extend((f"### {name}", "", f"- Reviewed rows: **{row.get('rows', 0):,}**"))
            for field, value in row.get("field_accuracy", {}).items():
                lines.append(f"- {field}: **{value:.1%}**")
            lines.append(f"- Event F1: **{row.get('event_f1', 0.0):.1%}**")
            lines.append("")
    lines.extend(("", "## Highest disagreements", ""))
    for row in payload["disagreements"]:
        if row.get("path"):
            lines.append(
                f"- [{row['canonical_news_id']}]({row['path']}) — "
                f"{len(row['field_mismatches'])} field mismatches, "
                f"event Jaccard {row['event_jaccard']:.1%}"
            )
    lines.append("")
    return "\n".join(lines)


def _disagreement_markdown(
    article: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
) -> str:
    return "\n".join((
        f"# {article.get('title') or article['canonical_news_id']}",
        "",
        f"- Canonical ID: `{article['canonical_news_id']}`",
        f"- Published UTC: `{article['published_at_utc']}`",
        f"- Tickers: `{', '.join(article.get('tickers') or []) or 'none'}`",
        "",
        f"## {first.get('model', 'first model')}",
        "",
        "```json",
        json.dumps(first["label"], ensure_ascii=False, indent=2),
        "```",
        "",
        f"## {second.get('model', 'second model')}",
        "",
        "```json",
        json.dumps(second["label"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Certified rendered article",
        "",
        "````text",
        str(article.get("rendered_text") or "").replace("````", "&#96;&#96;&#96;&#96;"),
        "````",
        "",
    ))
