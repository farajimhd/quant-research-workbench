from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "services" / "text-intelligence"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.text_intelligence.llm_issuer_labeling_v3.prompt import build_messages
from research.text_intelligence.llm_issuer_labeling_v3.schema import TRANSPORT_SCHEMA, canonicalize_output, validate_output
from services.model_gateway.config import GatewayConfig
from services.model_gateway.schemas import InferenceRequest
from services.model_gateway.service import ModelGateway
from text_intelligence.forecast_review import _load_system_prompt


DEFAULT_AUDIT = Path(r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v3\gold_audit_250_sol_v1")
DEFAULT_PROMPT = Path(r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\issuer_review_system_prompt_v1.txt")
DEFAULT_OUTPUT = Path(r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\prompt_calibration_v1.json")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def calibrate(prompt_path: Path, audit_root: Path, sample_size: int) -> dict:
    prompt = _load_system_prompt(prompt_path)
    samples = {str(row["source_id"]): row for row in read_jsonl(audit_root / "sample.jsonl")}
    answers = read_jsonl(audit_root / "answer_key.jsonl")
    positives = [row for row in answers if row["article_forecast_eligible"]]
    negatives = [row for row in answers if not row["article_forecast_eligible"]]
    half = max(1, sample_size // 2)
    selected = (positives[:half] + negatives[: max(1, sample_size - half)])[:sample_size]
    gateway = ModelGateway(GatewayConfig.from_env())
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    rows = []
    for answer in selected:
        sample = samples[str(answer["source_id"])]
        request = InferenceRequest(
            route="news.issuer_review.v1",
            idempotency_key=hashlib.sha256(f"prompt-calibration-v1|{prompt_hash}|{sample['source_id']}".encode()).hexdigest(),
            messages=build_messages(prompt, sample),
            response_schema=TRANSPORT_SCHEMA,
            metadata={"purpose": "prompt_calibration", "source_id": sample["source_id"], "prompt_hash": prompt_hash},
        )
        response = await gateway.infer(request)
        result = canonicalize_output(response.result)
        errors = validate_output(result, [int(row["sentence_id"]) for row in sample["normalized_sentences"]])
        if errors:
            raise ValueError(f"invalid calibration output for {sample['source_id']}: {errors}")
        predicted = {
            str(row.get("ticker") or "").upper(): row
            for row in result["issuers"] if row.get("ticker")
        }
        gold_units = {str(row["ticker"]).upper(): row for row in answer["issuer_units"]}
        article_prediction = any(float(row["forecast_relevance_probability"]) >= 0.5 for row in predicted.values())
        issuer_correct = sum(
            (float(predicted.get(ticker, {}).get("forecast_relevance_probability", 0)) >= 0.5)
            == (gold["forecast_eligibility"] == "eligible")
            for ticker, gold in gold_units.items()
        )
        rows.append({
            "source_id": sample["source_id"],
            "gold_article_forecast_eligible": bool(answer["article_forecast_eligible"]),
            "predicted_article_forecast_eligible": article_prediction,
            "gold_issuer_units": len(gold_units),
            "issuer_units_correct": issuer_correct,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "latency_ms": response.latency_ms,
            "cached": response.cached,
        })
    return {
        "contract_version": "news_issuer_review_prompt_calibration_v1",
        "prompt_sha256": prompt_hash,
        "sample_size": len(rows),
        "article_accuracy": sum(row["gold_article_forecast_eligible"] == row["predicted_article_forecast_eligible"] for row in rows) / len(rows),
        "issuer_accuracy": sum(row["issuer_units_correct"] for row in rows) / max(1, sum(row["gold_issuer_units"] for row in rows)),
        "total_cost_usd": sum(float(row["cost_usd"]) for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded live-API calibration of the frozen issuer-review prompt")
    parser.add_argument("--sample-size", type=int, default=4)
    parser.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not 2 <= args.sample_size <= 10:
        raise ValueError("sample-size must be between 2 and 10")
    result = asyncio.run(calibrate(args.prompt, args.audit_root, args.sample_size))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output.exists() and args.output.read_text(encoding="utf-8") != rendered:
        raise FileExistsError(f"existing calibration output differs: {args.output}")
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(json.dumps({key: result[key] for key in ("sample_size", "article_accuracy", "issuer_accuracy", "total_cost_usd")}, indent=2))


if __name__ == "__main__":
    main()
