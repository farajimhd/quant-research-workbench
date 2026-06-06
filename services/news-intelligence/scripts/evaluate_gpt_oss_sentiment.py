from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from news_intelligence.config import IntelligenceConfig
from news_intelligence.historical import article_key, load_articles_jsonl, write_jsonl


DEFAULT_CANDIDATES = [
    {"name": "low_48_chars1200", "max_tokens": 48, "max_text_chars": 1200},
    {"name": "low_64_chars1500", "max_tokens": 64, "max_text_chars": 1500},
    {"name": "low_96_chars3000", "max_tokens": 96, "max_text_chars": 3000},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate GPT-OSS sentiment accuracy and latency against supervised news.")
    parser.add_argument("--supervision-run", default="", help="Path to a codex_suppervision run directory. Defaults to latest.")
    parser.add_argument("--supervision-root", default=PACKAGE_ROOT / "codex_suppervision")
    parser.add_argument("--output-root", default=PACKAGE_ROOT / "pipeline_evaluation_runs" / "gpt_oss_sentiment")
    parser.add_argument("--article-limit", type=int, default=500)
    parser.add_argument("--model", default="", help="Override NEWS_INTELLIGENCE_LLM_MODEL.")
    parser.add_argument("--base-url", default="", help="Override NEWS_INTELLIGENCE_LLM_BASE_URL.")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--response-format", default="json_object")
    parser.add_argument("--timeout-ms", type=int, default=0)
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="Candidate as name:max_tokens:max_text_chars. Can be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = IntelligenceConfig.from_env()
    supervision_run = Path(args.supervision_run) if args.supervision_run else latest_run(Path(args.supervision_root))
    run_dir = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    articles = load_articles_jsonl(supervision_run / "selected_articles.jsonl")
    labels = load_supervision(supervision_run / "codex_supervision.jsonl")
    if args.article_limit > 0:
        articles = articles[: args.article_limit]

    client = GptOssClient(
        base_url=(args.base_url or config.llm_base_url).rstrip("/"),
        model=args.model or config.llm_model,
        reasoning_effort=args.reasoning_effort,
        response_format=args.response_format,
        timeout_ms=args.timeout_ms or config.llm_timeout_ms,
    )
    candidates = parse_candidates(args.candidate) if args.candidate else DEFAULT_CANDIDATES
    summaries = []
    for candidate in candidates:
        rows = evaluate_candidate(client, articles, labels, candidate)
        candidate_dir = run_dir / candidate["name"]
        candidate_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(candidate_dir / "gpt_oss_sentiment_results.jsonl", rows)
        summary = summarize_candidate(candidate, rows)
        summaries.append(summary)
        (candidate_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        (run_dir / "summary.json").write_text(json.dumps(build_run_summary(args, supervision_run, run_dir, client, summaries), indent=2, ensure_ascii=False), encoding="utf-8")
        (run_dir / "analysis.md").write_text(render_analysis(build_run_summary(args, supervision_run, run_dir, client, summaries)), encoding="utf-8")

    final = build_run_summary(args, supervision_run, run_dir, client, summaries)
    print(json.dumps({"run_dir": str(run_dir), "summary": final}, indent=2, ensure_ascii=False))
    return 0


class GptOssClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        reasoning_effort: str,
        response_format: str,
        timeout_ms: int,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.response_format = response_format
        self.timeout_ms = timeout_ms

    def classify_sentiment(self, article: Any, max_tokens: int, max_text_chars: int) -> dict[str, Any]:
        text = article.to_classification_article().classification_text(max_text_chars)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Reasoning effort: {self.reasoning_effort}. "
                        "Classify the direct trading sentiment of this news. "
                        "Return only one JSON object. No markdown. "
                        "Use exactly these keys: sentiment_label, sentiment_score, sentiment_confidence. "
                        "sentiment_label must be one of positive, negative, neutral. "
                        "sentiment_score must be a number from -1.0 to 1.0, where negative is bearish and positive is bullish. "
                        "sentiment_confidence must be a number from 0.0 to 1.0. "
                        "Use neutral unless the article implies a clear directional stock or market impact. "
                        "Historical performance listicles, generic market education, and broad commentary are neutral unless a fresh catalyst is present."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "title": article.title,
                            "publisher": article.publisher_name,
                            "tickers": article.tickers,
                            "published_at": article.published_at,
                            "text": text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.response_format:
            payload["response_format"] = {"type": self.response_format}

        started = time.perf_counter()
        try:
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_ms / 1000) as response:
                raw = json.loads(response.read().decode("utf-8"))
            seconds = time.perf_counter() - started
            message = raw["choices"][0]["message"]
            content = message.get("content")
            if not content:
                return {
                    "status": "failed",
                    "seconds": seconds,
                    "error": "empty_content",
                    "reasoning_chars": len(str(message.get("reasoning") or "")),
                    "usage": raw.get("usage", {}),
                    "raw_content": "",
                }
            parsed = normalize_sentiment(json.loads(extract_json(content)))
            return {
                "status": "completed",
                "seconds": seconds,
                "parsed": parsed,
                "usage": raw.get("usage", {}),
                "raw_content": content,
            }
        except (OSError, urllib.error.HTTPError, KeyError, TypeError, json.JSONDecodeError) as error:
            return {"status": "failed", "seconds": time.perf_counter() - started, "error": str(error)}


def evaluate_candidate(
    client: GptOssClient,
    articles: list[Any],
    labels: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for index, article in enumerate(articles, start=1):
        truth = labels.get(article_key(article))
        if not truth:
            continue
        output = client.classify_sentiment(
            article,
            max_tokens=int(candidate["max_tokens"]),
            max_text_chars=int(candidate["max_text_chars"]),
        )
        parsed = output.get("parsed", {})
        rows.append(
            {
                "candidate": candidate["name"],
                "row_index": index,
                "article_key": article_key(article),
                "article_id": article.article_id,
                "published_at": article.published_at,
                "title": article.title,
                "tickers": article.tickers,
                "truth_sentiment_label": truth.get("sentiment_label"),
                "truth_sentiment_score": truth.get("sentiment_score"),
                "prediction": parsed,
                "prediction_sentiment_label": parsed.get("sentiment_label", ""),
                "prediction_sentiment_score": parsed.get("sentiment_score"),
                "prediction_sentiment_confidence": parsed.get("sentiment_confidence"),
                "status": output.get("status", "unknown"),
                "seconds": output.get("seconds", 0.0),
                "error": output.get("error", ""),
                "usage": output.get("usage", {}),
            }
        )
    return rows


def summarize_candidate(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    completed = [row for row in rows if row["status"] == "completed"]
    labels = completed
    matches = sum(1 for row in labels if row["prediction_sentiment_label"] == row["truth_sentiment_label"])
    confusion = Counter(f"{row['truth_sentiment_label']} -> {row['prediction_sentiment_label']}" for row in labels)
    seconds = [float(row["seconds"] or 0.0) for row in rows]
    completed_seconds = [float(row["seconds"] or 0.0) for row in completed]
    score_errors = [
        abs(float(row["prediction_sentiment_score"] or 0.0) - float(row["truth_sentiment_score"] or 0.0))
        for row in completed
    ]
    return {
        **candidate,
        "article_count": total,
        "completed_count": len(completed),
        "failed_count": total - len(completed),
        "accuracy": matches / len(labels) if labels else 0.0,
        "matches": matches,
        "valid_label_count": sum(1 for row in completed if row["prediction_sentiment_label"]),
        "invalid_label_count": sum(1 for row in completed if not row["prediction_sentiment_label"]),
        "evaluated_labels": len(labels),
        "status_distribution": dict(Counter(row["status"] for row in rows)),
        "top_confusions": dict(confusion.most_common(12)),
        "sentiment_score_mae": statistics.mean(score_errors) if score_errors else 0.0,
        "elapsed_seconds_sum": sum(seconds),
        "mean_seconds": statistics.mean(seconds) if seconds else 0.0,
        "median_seconds": statistics.median(seconds) if seconds else 0.0,
        "p95_seconds": percentile(seconds, 0.95),
        "completed_mean_seconds": statistics.mean(completed_seconds) if completed_seconds else 0.0,
        "completed_p95_seconds": percentile(completed_seconds, 0.95),
        "throughput_articles_per_second": total / sum(seconds) if sum(seconds) else 0.0,
    }


def build_run_summary(
    args: argparse.Namespace,
    supervision_run: Path,
    run_dir: Path,
    client: GptOssClient,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "supervision_run": str(supervision_run),
        "output_run": str(run_dir),
        "article_limit": args.article_limit,
        "model": client.model,
        "base_url": client.base_url,
        "reasoning_effort": client.reasoning_effort,
        "response_format": client.response_format,
        "summaries": summaries,
        "best_by_accuracy": max(summaries, key=lambda row: (row["accuracy"], row["throughput_articles_per_second"]))["name"] if summaries else "",
        "best_by_speed": max(summaries, key=lambda row: row["throughput_articles_per_second"])["name"] if summaries else "",
        "best_balanced": max(summaries, key=lambda row: (row["accuracy"] * 0.75) + min(row["throughput_articles_per_second"] / 2.0, 1.0) * 0.25)["name"] if summaries else "",
    }


def render_analysis(summary: dict[str, Any]) -> str:
    lines = [
        "# GPT-OSS Sentiment Evaluation",
        "",
        f"Supervision run: `{summary['supervision_run']}`",
        f"Model: `{summary['model']}`",
        f"Reasoning effort: `{summary['reasoning_effort']}`",
        f"Response format: `{summary['response_format']}`",
        "",
        "| Candidate | Articles | Completed | Accuracy | Score MAE | Median s | p95 s | Throughput / s | Failed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["summaries"]:
        lines.append(
            "| {name} | {count} | {completed} | {acc:.3f} | {mae:.3f} | {median:.3f} | {p95:.3f} | {tps:.3f} | {failed} |".format(
                name=row["name"],
                count=row["article_count"],
                completed=row["completed_count"],
                acc=row["accuracy"],
                mae=row["sentiment_score_mae"],
                median=row["median_seconds"],
                p95=row["p95_seconds"],
                tps=row["throughput_articles_per_second"],
                failed=row["failed_count"],
            )
        )
    lines.extend(
        [
            "",
            f"Best by accuracy: `{summary['best_by_accuracy']}`",
            f"Best by speed: `{summary['best_by_speed']}`",
            f"Best balanced: `{summary['best_balanced']}`",
            "",
            "These metrics compare GPT-OSS against the local Codex supervision labels, not human-reviewed ground truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_supervision(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows[str(item["article_key"])] = item["output_contract"]
    return rows


def latest_run(root: Path) -> Path:
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No supervision runs found in {root}")
    return sorted(candidates)[-1]


def parse_candidates(values: list[str]) -> list[dict[str, Any]]:
    candidates = []
    for value in values:
        name, max_tokens, max_text_chars = value.split(":", 2)
        candidates.append({"name": name, "max_tokens": int(max_tokens), "max_text_chars": int(max_text_chars)})
    return candidates


def normalize_sentiment(parsed: dict[str, Any]) -> dict[str, Any]:
    label = str(parsed.get("sentiment_label") or "").strip().lower()
    if label not in {"positive", "negative", "neutral"}:
        label = normalize_label_alias(label)
    return {
        "sentiment_label": label if label in {"positive", "negative", "neutral"} else "",
        "sentiment_score": bounded_float(parsed.get("sentiment_score"), 0.0, -1.0, 1.0),
        "sentiment_confidence": bounded_float(parsed.get("sentiment_confidence"), 0.0, 0.0, 1.0),
    }


def normalize_label_alias(label: str) -> str:
    if label in {"bullish", "good", "favorable"}:
        return "positive"
    if label in {"bearish", "bad", "unfavorable"}:
        return "negative"
    if label in {"mixed", "flat", "none"}:
        return "neutral"
    return label


def extract_json(text: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text.strip()):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return json.dumps(value)
    return "{}"


def bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


if __name__ == "__main__":
    raise SystemExit(main())
