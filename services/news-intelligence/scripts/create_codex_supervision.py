from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from news_intelligence.historical import article_key, load_historical_articles, sample_articles, write_jsonl
from news_intelligence.supervision import CodexSilverSupervisor, SUPERVISOR_VERSION, response_to_jsonable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create deterministic Codex silver supervision labels for Benzinga news.")
    parser.add_argument("--raw-root", default=r"D:\TradingData\stock-news-analysis\news_discovery_v5\raw")
    parser.add_argument("--output-root", default=PACKAGE_ROOT / "codex_suppervision")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--start-date", default="", help="Optional inclusive YYYY-MM-DD filter.")
    parser.add_argument("--end-date", default="", help="Optional inclusive YYYY-MM-DD filter.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    articles = load_historical_articles(
        Path(args.raw_root),
        provider="benzinga",
        start_date=parse_date(args.start_date),
        end_date=parse_date(args.end_date),
    )
    selected = sample_articles(articles, args.sample_size, args.seed)
    supervisor = CodexSilverSupervisor()
    rows = []
    for index, article in enumerate(selected, start=1):
        classification_article = article.to_classification_article()
        response = supervisor.classify(classification_article)
        rows.append(
            {
                "row_index": index,
                "article_key": article_key(article),
                "article_id": article.article_id,
                "provider": article.provider,
                "published_at": article.published_at,
                "title": article.title,
                "tickers": article.tickers,
                "source_path": article.source_path,
                "supervision_kind": "deterministic_silver_labels",
                "supervisor_version": SUPERVISOR_VERSION,
                "output_contract": response_to_jsonable(response),
            }
        )

    write_jsonl(run_dir / "selected_articles.jsonl", [article.to_dict() for article in selected])
    write_jsonl(run_dir / "codex_supervision.jsonl", rows)
    summary = build_summary(args, articles, selected, rows)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "analysis.md").write_text(render_analysis(summary), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "summary": summary}, indent=2, ensure_ascii=False))
    return 0


def parse_date(value: str):
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def build_summary(args: argparse.Namespace, articles: list[Any], selected: list[Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    event_counter = Counter(row["output_contract"]["event_type"] for row in rows)
    sentiment_counter = Counter(row["output_contract"]["sentiment_label"] for row in rows)
    evidence_counter = Counter(row["output_contract"]["evidence_basis"] for row in rows)
    ticker_counts = Counter("has_ticker" if row["tickers"] else "no_ticker" for row in rows)
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_root": str(args.raw_root),
        "provider": "benzinga",
        "supervision_kind": "deterministic_silver_labels",
        "supervisor_version": SUPERVISOR_VERSION,
        "seed": args.seed,
        "available_articles": len(articles),
        "selected_articles": len(selected),
        "event_distribution": dict(event_counter),
        "sentiment_distribution": dict(sentiment_counter),
        "evidence_distribution": dict(evidence_counter),
        "ticker_distribution": dict(ticker_counts),
    }


def render_analysis(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Codex Supervision Set",
            "",
            "These labels are deterministic silver labels produced by Codex-designed rules. They are useful for repeatable pipeline comparison, but they are not human ground truth.",
            "",
            f"Provider: `{summary['provider']}`",
            f"Available articles: `{summary['available_articles']}`",
            f"Selected articles: `{summary['selected_articles']}`",
            f"Supervisor version: `{summary['supervisor_version']}`",
            "",
            "## Distributions",
            "",
            f"Events: `{json.dumps(summary['event_distribution'], ensure_ascii=False)}`",
            f"Sentiment: `{json.dumps(summary['sentiment_distribution'], ensure_ascii=False)}`",
            f"Evidence: `{json.dumps(summary['evidence_distribution'], ensure_ascii=False)}`",
            f"Ticker presence: `{json.dumps(summary['ticker_distribution'], ensure_ascii=False)}`",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
