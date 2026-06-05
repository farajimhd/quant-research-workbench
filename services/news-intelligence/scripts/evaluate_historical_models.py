from __future__ import annotations

import argparse
import gc
import html
import json
import os
import re
import random
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCOPE_LABELS = [
    "single_equity",
    "multi_equity",
    "macro_geopolitical",
    "crypto",
    "commodity",
    "broad_market",
    "etf",
    "no_ticker_context",
]

EVENT_LABELS = [
    "earnings",
    "analyst_rating",
    "capital_markets",
    "merger_acquisition",
    "fda_biotech",
    "legal_regulatory",
    "macro_geopolitical",
    "product_contract",
    "insider_ownership",
    "crypto",
    "other",
]

ENTITY_LABELS = [
    "company",
    "ticker",
    "person",
    "regulator",
    "product",
    "country",
    "commodity",
    "crypto asset",
    "financial metric",
    "event",
]

LLM_SYSTEM_PROMPT = """You classify financial news for a live trading scanner.
Return strict JSON only. Do not include markdown.
Use the article text and known tickers. Do not invent tickers.

JSON schema:
{
  "summary": short factual one-sentence summary for display,
  "event_type": one of ["earnings","analyst_rating","capital_markets","merger_acquisition","fda_biotech","legal_regulatory","macro_geopolitical","product_contract","insider_ownership","crypto","other"],
  "event_subtype": short string,
  "sentiment_overall": one of ["positive","negative","neutral","mixed"],
  "materiality_score": number from 0 to 1, where 1 means likely scanner-relevant during the trading session,
  "urgency_score": number from 0 to 1, where 1 means the market may react immediately,
  "time_horizon": one of ["intraday","session_to_multi_day","longer_term","contextual","unknown"],
  "affected_tickers": [{"ticker": "AAPL", "sentiment": "positive|negative|neutral|mixed", "direction_score": -1 to 1, "confidence": 0 to 1}],
  "content_completeness": one of ["full_text","short_body","title_only","pdf_enriched","url_enriched"],
  "evidence_basis": one of ["title_only","title_teaser","title_body","title_url_extract","title_pdf_extract","provider_insights"],
  "labels": short array of useful tags,
  "rationale": one concise sentence
}"""


@dataclass
class Article:
    article_id: str
    provider: str
    published_at: str
    title: str
    text: str
    tickers: list[str]
    source_path: str

    def formatted_for_models(self, max_chars: int) -> str:
        tickers = ", ".join(self.tickers) if self.tickers else "none"
        body = self.text[:max_chars].strip()
        return f"Title: {self.title.strip()}\nTickers: {tickers}\nBody: {body}"


@dataclass
class ModelEval:
    key: str
    repo_id: str
    task: str
    tier: str
    local_path: Path
    size_gb: float
    status: str = "pending"
    error: str = ""
    load_seconds: float = 0.0
    article_seconds: list[float] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)

    def profile_row(self) -> dict[str, Any]:
        return {
            "model": self.key,
            "repo_id": self.repo_id,
            "task": self.task,
            "tier": self.tier,
            "local_path": str(self.local_path),
            "size_gb": self.size_gb,
            "status": self.status,
            "error": self.error,
            "load_seconds": self.load_seconds,
            "article_count": len(self.article_seconds),
            "mean_article_seconds": statistics.mean(self.article_seconds) if self.article_seconds else None,
            "median_article_seconds": statistics.median(self.article_seconds) if self.article_seconds else None,
            "p95_article_seconds": percentile(self.article_seconds, 0.95),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate local news-intelligence models on historical news.")
    parser.add_argument("--raw-root", default=r"D:\TradingData\stock-news-analysis\news_discovery_v5\raw")
    parser.add_argument("--model-root", default=r"D:\models_artifacts\opensource")
    parser.add_argument("--manifest", default=Path(__file__).resolve().parents[1] / "models" / "opensource_models.json")
    parser.add_argument("--output-root", default=Path(__file__).resolve().parents[1] / "evaluation_runs")
    parser.add_argument("--week-start", default="2025-09-03")
    parser.add_argument("--article-limit", type=int, default=24)
    parser.add_argument("--llm-article-limit", type=int, default=3)
    parser.add_argument("--max-text-chars", type=int, default=1800)
    parser.add_argument("--max-model-gb", type=float, default=4.5)
    parser.add_argument("--include-large", action="store_true", help="Attempt models larger than --max-model-gb.")
    parser.add_argument("--models", nargs="*", default=None, help="Optional model keys to evaluate.")
    parser.add_argument("--random-sample", action="store_true", help="Randomly sample from the selected week instead of taking chronological rows.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--trust-remote-code", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    week_start = datetime.fromisoformat(args.week_start).date()
    week_end = week_start + timedelta(days=6)
    articles = load_week_articles(Path(args.raw_root), week_start, week_end)
    selected_articles = stratified_sample(articles, args.article_limit, args.random_sample, args.seed)
    write_jsonl(run_dir / "selected_articles.jsonl", [article.__dict__ for article in selected_articles])

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    model_items = manifest.get("models", [])
    if args.models:
        allowed = set(args.models)
        model_items = [item for item in model_items if item["key"] in allowed]

    result_rows: list[dict[str, Any]] = []
    profile_rows: list[dict[str, Any]] = []
    for item in model_items:
        evaluation = evaluate_model(item, Path(args.model_root), selected_articles, args, run_dir)
        profile_rows.append(evaluation.profile_row())
        for output in evaluation.outputs:
            result_rows.append(output)
        write_jsonl(run_dir / "profiling.jsonl", profile_rows)
        write_jsonl(run_dir / "model_results.jsonl", result_rows)
        cleanup_runtime()

    summary = build_summary(articles, selected_articles, profile_rows, result_rows, week_start, week_end)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "analysis.md").write_text(render_analysis(summary), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "summary": summary}, indent=2, ensure_ascii=False))
    return 0


def load_week_articles(raw_root: Path, start: date, end: date) -> list[Article]:
    rows: list[Article] = []
    for path in raw_root.rglob("*.json"):
        provider = "benzinga" if "benzinga" in [part.lower() for part in path.parts] else "massive"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in normalize_payload(payload):
            published = parse_dt(item.get("published") or item.get("published_utc"))
            if not published or not (start <= published.date() <= end):
                continue
            article_id = str(item.get("benzinga_id") or item.get("id") or path.stem)
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or item.get("description") or item.get("teaser") or "").strip()
            body = clean_html(body)
            pdf_text = "\n".join(str(pdf.get("text", "")) for pdf in item.get("pdfs", []) if isinstance(pdf, dict))
            text = "\n".join(part for part in [body, pdf_text] if part).strip()
            tickers = normalize_tickers(item.get("tickers") or ticker_from_insights(item.get("insights")))
            if not title:
                continue
            rows.append(
                Article(
                    article_id=article_id,
                    provider=provider,
                    published_at=published.isoformat(),
                    title=title,
                    text=text or title,
                    tickers=tickers,
                    source_path=str(path),
                )
            )
    rows.sort(key=lambda item: (item.published_at, item.provider, item.article_id))
    return rows


def normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def stratified_sample(articles: list[Article], limit: int, random_sample: bool, seed: int) -> list[Article]:
    if limit <= 0 or len(articles) <= limit:
        return articles
    buckets: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        key = f"{article.provider}:{'ticker' if article.tickers else 'no_ticker'}"
        buckets[key].append(article)
    selected: list[Article] = []
    per_bucket = max(1, limit // max(len(buckets), 1))
    rng = random.Random(seed)
    for bucket in sorted(buckets):
        rows = list(buckets[bucket])
        if random_sample:
            rng.shuffle(rows)
        selected.extend(rows[:per_bucket])
    if len(selected) < limit:
        seen = {article.source_path for article in selected}
        remainder = [article for article in articles if article.source_path not in seen]
        if random_sample:
            rng.shuffle(remainder)
        selected.extend(remainder)
    return selected[:limit]


def evaluate_model(item: dict[str, Any], root: Path, articles: list[Article], args: argparse.Namespace, run_dir: Path) -> ModelEval:
    key = item["key"]
    local_path = root / key
    evaluation = ModelEval(
        key=key,
        repo_id=item.get("repo_id", ""),
        task=item.get("task", ""),
        tier=item.get("tier", ""),
        local_path=local_path,
        size_gb=directory_size_gb(local_path),
    )
    if not local_path.exists():
        evaluation.status = "skipped"
        evaluation.error = "model_not_downloaded"
        return evaluation
    if evaluation.size_gb > args.max_model_gb and not args.include_large:
        evaluation.status = "skipped"
        evaluation.error = f"model_size_exceeds_limit_{args.max_model_gb:g}gb"
        return evaluation
    article_subset = articles
    if evaluation.tier in {"small_llm", "offline_research_llm"}:
        prioritized = [article for article in articles if article.tickers] + [article for article in articles if not article.tickers]
        article_subset = prioritized[: args.llm_article_limit]
    try:
        started = time.perf_counter()
        runner = build_runner(evaluation, args)
        evaluation.load_seconds = time.perf_counter() - started
    except Exception as error:
        evaluation.status = "failed_load"
        evaluation.error = repr(error)
        return evaluation
    evaluation.status = "completed"
    for article in article_subset:
        started = time.perf_counter()
        try:
            output = runner(article, args.max_text_chars)
            error = ""
        except Exception as exc:
            output = {}
            error = repr(exc)
        elapsed = time.perf_counter() - started
        evaluation.article_seconds.append(elapsed)
        evaluation.outputs.append(
            {
                "model": key,
                "repo_id": evaluation.repo_id,
                "task": evaluation.task,
                "tier": evaluation.tier,
                "article_id": article.article_id,
                "provider": article.provider,
                "published_at": article.published_at,
                "title": article.title,
                "tickers": article.tickers,
                "elapsed_seconds": elapsed,
                "error": error,
                "output": output,
            }
        )
    return evaluation


def build_runner(evaluation: ModelEval, args: argparse.Namespace):
    task = evaluation.task
    path = str(evaluation.local_path)
    if task == "zero_shot_ner":
        from gliner import GLiNER

        model = GLiNER.from_pretrained(path)

        def run(article: Article, max_chars: int) -> dict[str, Any]:
            text = article.formatted_for_models(max_chars)
            entities = model.predict_entities(text, ENTITY_LABELS, threshold=0.35)
            return {"prompt_labels": ENTITY_LABELS, "entities": entities[:30]}

        return run
    if task == "zero_shot_classification":
        from transformers import pipeline

        classifier = pipeline("zero-shot-classification", model=path, tokenizer=path, device=-1)

        def run(article: Article, max_chars: int) -> dict[str, Any]:
            text = article.formatted_for_models(max_chars)
            scope = classifier(text, SCOPE_LABELS, multi_label=True)
            event = classifier(text, EVENT_LABELS, multi_label=True)
            return {
                "scope_prompt_labels": SCOPE_LABELS,
                "event_prompt_labels": EVENT_LABELS,
                "scope": top_labels(scope),
                "event": top_labels(event),
            }

        return run
    if task in {"instruction_llm", "financial_llm"}:
        from transformers import pipeline

        generator = pipeline(
            "text-generation",
            model=path,
            tokenizer=path,
            device=-1 if args.device == "cpu" else 0,
            trust_remote_code=args.trust_remote_code,
        )

        def run(article: Article, max_chars: int) -> dict[str, Any]:
            if evaluation.repo_id.startswith("openai/gpt-oss"):
                messages = [
                    {"role": "system", "content": LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": build_llm_user_prompt(article, max_chars)},
                ]
                prompt = json.dumps(messages, ensure_ascii=False)
                output = generator(messages, max_new_tokens=320, do_sample=False, temperature=None, return_full_text=False)
            else:
                prompt = build_llm_prompt(article, max_chars)
                output = generator(prompt, max_new_tokens=320, do_sample=False, temperature=None, return_full_text=False)
            text = extract_generated_text(output)
            return {"prompt": prompt, "raw_text": text, "parsed_json": parse_json_object(text)}

        return run
    if task == "token_classification":
        extractor = build_token_classifier(path)

        def run(article: Article, max_chars: int) -> dict[str, Any]:
            text = article.formatted_for_models(max_chars)
            entities = extractor(text[:1200])
            return {"format_prompt": "Extract named financial entities from the formatted article.", "entities": entities[:30]}

        return run
    classifier = build_text_classifier(path)

    def run(article: Article, max_chars: int) -> dict[str, Any]:
        text = article.formatted_for_models(max_chars)
        output = classifier(text[:900], truncation=True)
        return {
            "format_prompt": (
                "Classify financial sentiment from a normalized article block with Title, Tickers, and Body. "
                "Map the model label to positive, negative, or neutral when possible."
            ),
            "raw": output,
            "normalized": normalize_classifier_output(output),
        }

    return run


def build_llm_prompt(article: Article, max_chars: int) -> str:
    return (
        f"{LLM_SYSTEM_PROMPT}\n\n"
        f"{build_llm_user_prompt(article, max_chars)}\n\n"
        "Return JSON now:"
    )


def build_llm_user_prompt(article: Article, max_chars: int) -> str:
    text = article.formatted_for_models(max_chars)
    completeness = "title_only" if not article.text or article.text.strip() == article.title.strip() else "full_text"
    evidence_basis = "title_only" if completeness == "title_only" else "title_body"
    if completeness == "title_only":
        instruction = (
            "The article has no body text. Classify only from title, tickers, and provider metadata. "
            "Do not infer details that are not present; lower confidence unless the title is explicit."
        )
    else:
        instruction = "Classify from the title, known tickers, and available article text."
    return (
        f"{instruction}\n"
        f"content_completeness_hint: {completeness}\n"
        f"evidence_basis_hint: {evidence_basis}\n\n"
        "Article:\n"
        f"{text}\n\n"
        "Return JSON now."
    )


def extract_generated_text(output: Any) -> str:
    if not output:
        return ""
    item = output[0] if isinstance(output, list) else output
    if not isinstance(item, dict):
        return str(item)
    generated = item.get("generated_text", "")
    if isinstance(generated, list) and generated:
        last = generated[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
        return str(last)
    return str(generated)


def top_labels(output: dict[str, Any], n: int = 5) -> list[dict[str, Any]]:
    labels = output.get("labels", [])
    scores = output.get("scores", [])
    return [{"label": label, "score": float(score)} for label, score in list(zip(labels, scores))[:n]]


def normalize_classifier_output(output: Any) -> dict[str, Any]:
    item = output[0] if isinstance(output, list) and output else output
    if not isinstance(item, dict):
        return {"label": "unknown", "score": 0.0}
    label = str(item.get("label", "unknown")).lower()
    score = float(item.get("score", 0.0))
    if "positive" in label or label in {"label_2", "bullish"}:
        normalized = "positive"
    elif "negative" in label or label in {"label_0", "bearish"}:
        normalized = "negative"
    elif "neutral" in label or label in {"label_1"}:
        normalized = "neutral"
    else:
        normalized = label
    return {"label": normalized, "score": score}


def build_summary(
    week_articles: list[Article],
    selected_articles: list[Article],
    profiles: list[dict[str, Any]],
    results: list[dict[str, Any]],
    week_start: date,
    week_end: date,
) -> dict[str, Any]:
    model_outputs = defaultdict(list)
    for row in results:
        model_outputs[row["model"]].append(row)
    model_summaries = []
    for profile in profiles:
        rows = model_outputs.get(profile["model"], [])
        sentiment_counter = Counter()
        error_count = 0
        sample_outputs = []
        for row in rows:
            if row.get("error"):
                error_count += 1
            normalized = row.get("output", {}).get("normalized")
            if normalized:
                sentiment_counter[normalized.get("label", "unknown")] += 1
            parsed = row.get("output", {}).get("parsed_json")
            if parsed:
                sentiment_counter[str(parsed.get("sentiment_overall", "unknown"))] += 1
            if len(sample_outputs) < 3:
                sample_outputs.append({"title": row["title"], "output": row["output"], "error": row.get("error", "")})
        model_summaries.append(
            {
                **profile,
                "error_count": error_count,
                "sentiment_distribution": dict(sentiment_counter),
                "sample_outputs": sample_outputs,
            }
        )
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_article_count": len(week_articles),
        "selected_article_count": len(selected_articles),
        "provider_distribution": dict(Counter(article.provider for article in selected_articles)),
        "ticker_article_count": sum(1 for article in selected_articles if article.tickers),
        "models": model_summaries,
    }


def render_analysis(summary: dict[str, Any]) -> str:
    lines = [
        "# Historical News Model Evaluation",
        "",
        f"Week: {summary['week_start']} to {summary['week_end']}",
        f"Week articles discovered: {summary['week_article_count']}",
        f"Articles evaluated: {summary['selected_article_count']}",
        f"Provider distribution: {summary['provider_distribution']}",
        "",
        "## Model Comparison",
        "",
        "| Model | Task | Status | Size GB | Load s | Median/article s | Errors | Sentiment distribution |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for model in summary["models"]:
        lines.append(
            "| {model} | {task} | {status} | {size_gb:.2f} | {load_seconds:.2f} | {median} | {errors} | {dist} |".format(
                model=model["model"],
                task=model["task"],
                status=model["status"],
                size_gb=float(model["size_gb"] or 0),
                load_seconds=float(model["load_seconds"] or 0),
                median=fmt(model.get("median_article_seconds")),
                errors=model["error_count"],
                dist=json.dumps(model["sentiment_distribution"], ensure_ascii=False),
            )
        )
    lines.extend(["", "## Notes", ""])
    completed = [model for model in summary["models"] if model["status"] == "completed"]
    skipped = [model for model in summary["models"] if model["status"] != "completed"]
    if completed:
        fastest = min(completed, key=lambda item: item.get("median_article_seconds") or 999999)
        lines.append(f"- Fastest completed model by median article latency: `{fastest['model']}`.")
    for model in skipped:
        lines.append(f"- `{model['model']}` did not complete: {model['status']} / {model.get('error','')}.")
    lines.append("- Treat this as an operational benchmark, not ground truth accuracy; the archive has no human labels.")
    return "\n".join(lines) + "\n"


def fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}"


def clean_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def ticker_from_insights(insights: Any) -> list[str]:
    if not isinstance(insights, list):
        return []
    return [str(item.get("ticker", "")) for item in insights if isinstance(item, dict)]


def normalize_tickers(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    tickers = []
    for value in values:
        text = str(value).strip().upper()
        if text and text not in tickers:
            tickers.append(text)
    return tickers


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else {}
    return {}


def build_text_classifier(path: str):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(path)
    model.eval()
    id2label = getattr(model.config, "id2label", {}) or {}

    def classify(text: str, truncation: bool = True) -> list[dict[str, Any]]:
        encoded = tokenizer(
            text,
            truncation=truncation,
            max_length=512,
            return_tensors="pt",
            return_token_type_ids=False,
        )
        with torch.no_grad():
            logits = model(**encoded).logits[0]
            probabilities = torch.softmax(logits, dim=-1)
        index = int(torch.argmax(probabilities).item())
        label = str(id2label.get(index, f"label_{index}"))
        return [{"label": label, "score": float(probabilities[index].item())}]

    return classify


def build_token_classifier(path: str):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path)
    model = AutoModelForTokenClassification.from_pretrained(path)
    model.eval()
    id2label = getattr(model.config, "id2label", {}) or {}

    def extract(text: str) -> list[dict[str, Any]]:
        encoded = tokenizer(
            text,
            truncation=True,
            max_length=512,
            return_offsets_mapping=True,
            return_tensors="pt",
            return_token_type_ids=False,
        )
        offsets = encoded.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = model(**encoded).logits[0]
            probabilities = torch.softmax(logits, dim=-1)
            label_ids = torch.argmax(probabilities, dim=-1).tolist()
        entities: list[dict[str, Any]] = []
        for token_index, label_id in enumerate(label_ids):
            start, end = offsets[token_index]
            if start == end:
                continue
            label = str(id2label.get(label_id, f"label_{label_id}"))
            if label in {"O", "LABEL_0", "label_0"}:
                continue
            entities.append(
                {
                    "text": text[start:end],
                    "label": label,
                    "score": float(probabilities[token_index, label_id].item()),
                    "start": start,
                    "end": end,
                }
            )
        return entities

    return extract


def directory_size_gb(path: Path) -> float:
    total = 0
    if path.exists():
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    return round(total / (1024**3), 4)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def cleanup_runtime() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
