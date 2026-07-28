from __future__ import annotations

import json
import statistics
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

from research.news_labeling.gpt_oss_v1.compare import AGREEMENT_FIELDS
from research.news_labeling.gpt_oss_v1.data import read_jsonl


def write_multi_model_comparison(
    *,
    sample_path: Path,
    model_roots: list[Path],
    output_root: Path,
    disagreement_limit: int,
    answer_key_path: Path | None = None,
) -> Path:
    sample = read_jsonl(sample_path)
    sample_by_id = {str(row["canonical_news_id"]): row for row in sample}
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for root in model_roots:
        labels = {
            str(row["canonical_news_id"]): row
            for row in read_jsonl(root / "labels.jsonl")
            if row.get("status") == "completed"
        }
        unknown = set(labels) - set(sample_by_id)
        if unknown:
            raise RuntimeError(
                f"{root.name} contains {len(unknown)} identities outside the frozen sample."
            )
        drift = [
            identifier
            for identifier, row in labels.items()
            if row.get("text_sha256") != sample_by_id[identifier].get("text_sha256")
        ]
        if drift:
            raise RuntimeError(
                f"{root.name} has rendered-text drift for {len(drift)} identities."
            )
        runs[root.name] = labels
        manifests[root.name] = _read_json(root / "manifest.json")

    pairwise: dict[str, Any] = {}
    for left_name, right_name in combinations(runs, 2):
        common = sorted(set(runs[left_name]) & set(runs[right_name]))
        pairwise[f"{left_name}__{right_name}"] = _pair_agreement(
            runs[left_name], runs[right_name], common
        )

    all_common = sorted(set.intersection(*(set(rows) for rows in runs.values())))
    answer_key = _answer_key(answer_key_path)
    accuracy = {
        name: _accuracy(rows, answer_key) for name, rows in runs.items()
    } if answer_key else {}
    disagreements = _rank_multi_disagreements(runs, all_common)
    output_root.mkdir(parents=True, exist_ok=True)
    packet_root = output_root / "disagreements"
    packet_root.mkdir(exist_ok=True)
    for rank, row in enumerate(disagreements[: max(0, disagreement_limit)], start=1):
        path = packet_root / f"{rank:03d}-{row['canonical_news_id']}.md"
        path.write_text(
            _disagreement_markdown(
                sample_by_id[row["canonical_news_id"]],
                {name: runs[name][row["canonical_news_id"]] for name in runs},
            ),
            encoding="utf-8",
        )
        row["path"] = str(path.relative_to(output_root)).replace("\\", "/")

    payload = {
        "sample_rows": len(sample),
        "model_order": list(runs),
        "all_model_common_rows": len(all_common),
        "models": {
            name: {
                "completed_rows": len(runs[name]),
                "failed_rows": int(manifests[name].get("failed_rows") or 0),
                "prompt_tokens": int(manifests[name].get("prompt_tokens") or 0),
                "completion_tokens": int(
                    manifests[name].get("completion_tokens") or 0
                ),
                "conservative_actual_cost_usd": str(
                    manifests[name].get("conservative_actual_cost_usd") or "0"
                ),
                "batch_elapsed_seconds": int(
                    manifests[name].get("batch_elapsed_seconds") or 0
                ),
                "distributions": _distributions(runs[name].values()),
            }
            for name in runs
        },
        "pairwise": pairwise,
        "answer_key_path": str(answer_key_path) if answer_key_path else None,
        "accuracy": accuracy,
        "disagreements": disagreements,
    }
    (output_root / "comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = output_root / "COMPARISON.md"
    report.write_text(_comparison_markdown(payload), encoding="utf-8")
    return report


def _pair_agreement(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    identities: list[str],
) -> dict[str, Any]:
    if not identities:
        return {"rows": 0}
    fields = {
        name: sum(
            _value(left[item]["label"], path) == _value(right[item]["label"], path)
            for item in identities
        )
        / len(identities)
        for name, path in AGREEMENT_FIELDS.items()
    }
    jaccards: list[float] = []
    exact = 0
    for item in identities:
        first = _event_set(left[item]["label"])
        second = _event_set(right[item]["label"])
        union = first | second
        jaccards.append(len(first & second) / len(union) if union else 1.0)
        exact += first == second
    return {
        "rows": len(identities),
        "field_accuracy": fields,
        "mean_field_accuracy": statistics.fmean(fields.values()),
        "event_exact_match": exact / len(identities),
        "event_mean_jaccard": statistics.fmean(jaccards),
    }


def _rank_multi_disagreements(
    runs: dict[str, dict[str, dict[str, Any]]],
    identities: list[str],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for identifier in identities:
        field_consensus: dict[str, float] = {}
        for name, path in AGREEMENT_FIELDS.items():
            values = [
                json.dumps(_value(rows[identifier]["label"], path), sort_keys=True)
                for rows in runs.values()
            ]
            field_consensus[name] = max(Counter(values).values()) / len(values)
        event_sets = [
            _event_set(rows[identifier]["label"]) for rows in runs.values()
        ]
        pair_jaccards: list[float] = []
        for first, second in combinations(event_sets, 2):
            union = first | second
            pair_jaccards.append(len(first & second) / len(union) if union else 1.0)
        event_consensus = statistics.fmean(pair_jaccards) if pair_jaccards else 1.0
        score = sum(1.0 - value for value in field_consensus.values()) + (
            1.0 - event_consensus
        )
        if score:
            ranked.append(
                {
                    "canonical_news_id": identifier,
                    "score": score,
                    "field_consensus": field_consensus,
                    "event_pairwise_mean_jaccard": event_consensus,
                }
            )
    return sorted(ranked, key=lambda row: (-row["score"], row["canonical_news_id"]))


def _distributions(rows: Any) -> dict[str, dict[str, int]]:
    counters: dict[str, Counter[str]] = {
        "source.origin": Counter(),
        "source.role": Counter(),
        "source.issuer_relationship": Counter(),
        "sentiment.overall": Counter(),
        "novelty.class": Counter(),
        "event.family": Counter(),
    }
    for row in rows:
        label = row["label"]
        for name, path in AGREEMENT_FIELDS.items():
            if name in counters:
                counters[name][str(_value(label, path))] += 1
        for event in label.get("events", []):
            if isinstance(event, dict):
                counters["event.family"][str(event.get("family"))] += 1
    return {name: dict(counter.most_common()) for name, counter in counters.items()}


def _comparison_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# OpenAI remote-model news-label comparison",
        "",
        f"- Frozen sample: **{payload['sample_rows']:,}**",
        f"- Rows completed by every model: **{payload['all_model_common_rows']:,}**",
        "",
        "Agreement is not semantic accuracy. This packet compares model stability "
        "until a reviewed answer key is available.",
        "",
        "## Completion and cost",
        "",
        "| Model | Completed | Failed | Batch elapsed | Input tokens | Output tokens | Conservative cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in payload["model_order"]:
        row = payload["models"][name]
        lines.append(
            f"| {name} | {row['completed_rows']:,} | {row['failed_rows']:,} | "
            f"{_duration(row['batch_elapsed_seconds'])} | "
            f"{row['prompt_tokens']:,} | {row['completion_tokens']:,} | "
            f"${float(row['conservative_actual_cost_usd']):.4f} |"
        )
    lines.extend(
        (
            "",
            "## Pairwise agreement",
            "",
            "| Pair | Common | Mean fields | Event exact | Event Jaccard |",
            "|---|---:|---:|---:|---:|",
        )
    )
    for pair, row in payload["pairwise"].items():
        lines.append(
            f"| {pair.replace('__', ' / ')} | {row.get('rows', 0):,} | "
            f"{row.get('mean_field_accuracy', 0.0):.1%} | "
            f"{row.get('event_exact_match', 0.0):.1%} | "
            f"{row.get('event_mean_jaccard', 0.0):.1%} |"
        )
    lines.extend(("", "## Semantic accuracy", ""))
    if not payload["accuracy"]:
        lines.append(
            "No reviewed answer key was supplied. Agreement is not accuracy; "
            "use `--answer-key-jsonl` before selecting a semantic winner."
        )
    else:
        lines.extend(
            (
                "| Model | Reviewed | Mean field accuracy | Event precision | Event recall | Event F1 |",
                "|---|---:|---:|---:|---:|---:|",
            )
        )
        for name in payload["model_order"]:
            row = payload["accuracy"][name]
            fields = list(row.get("field_accuracy", {}).values())
            mean_field = statistics.fmean(fields) if fields else 0.0
            lines.append(
                f"| {name} | {row.get('rows', 0):,} | {mean_field:.1%} | "
                f"{row.get('event_precision', 0.0):.1%} | "
                f"{row.get('event_recall', 0.0):.1%} | "
                f"{row.get('event_f1', 0.0):.1%} |"
            )
    lines.extend(("", "## Highest multi-model disagreements", ""))
    for row in payload["disagreements"]:
        if row.get("path"):
            lines.append(
                f"- [{row['canonical_news_id']}]({row['path']}) - "
                f"score {row['score']:.3f}, event agreement "
                f"{row['event_pairwise_mean_jaccard']:.1%}"
            )
    lines.append("")
    return "\n".join(lines)


def _disagreement_markdown(
    article: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> str:
    lines = [
        f"# {article.get('title') or article['canonical_news_id']}",
        "",
        f"- Canonical ID: `{article['canonical_news_id']}`",
        f"- Published UTC: `{article['published_at_utc']}`",
        f"- Tickers: `{', '.join(article.get('tickers') or []) or 'none'}`",
        "",
    ]
    for name, row in results.items():
        lines.extend(
            (
                f"## {name}",
                "",
                "```json",
                json.dumps(row["label"], ensure_ascii=False, indent=2),
                "```",
                "",
            )
        )
    lines.extend(
        (
            "## Certified rendered article",
            "",
            "````text",
            str(article.get("rendered_text") or "").replace(
                "````", "&#96;&#96;&#96;&#96;"
            ),
            "````",
            "",
        )
    )
    return "\n".join(lines)


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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _answer_key(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    rows = {
        str(row["canonical_news_id"]): row["label"]
        for row in read_jsonl(path)
        if isinstance(row.get("label"), dict)
    }
    if not rows:
        raise RuntimeError(f"Answer key has no rows with a label object: {path}")
    return rows


def _accuracy(
    predictions: dict[str, dict[str, Any]],
    answer_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    identities = sorted(set(predictions) & set(answer_key))
    if not identities:
        return {"rows": 0}
    fields = {
        name: sum(
            _value(predictions[item]["label"], path)
            == _value(answer_key[item], path)
            for item in identities
        )
        / len(identities)
        for name, path in AGREEMENT_FIELDS.items()
    }
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
        "event_f1": (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        ),
    }


def _duration(seconds: int) -> str:
    if not seconds:
        return "-"
    minutes, remaining = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return (
        f"{hours}h {minutes}m"
        if hours
        else f"{minutes}m {remaining}s"
        if minutes
        else f"{remaining}s"
    )
