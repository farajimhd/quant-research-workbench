from __future__ import annotations

import hashlib
import html
import json
import math
import os
import random
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research.mlops.env import discover_env_files, load_env_files
from research.news_labeling.openai_batch_v1.openai_api import OpenAIClient

from .prompt import build_messages, build_system_prompt, example_source_ids, load_example_bank
from .schema import SCHEMA_VERSION, TRANSPORT_SCHEMA, canonicalize_output, normalize_ticker, validate_output


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUDIT_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\consolidated_gold_audit_v1_v48_baseline_20260810")
DEFAULT_GOLD_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\gold_certified_news_labels_consolidated_v1")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v3\gold_audit_250_sol_v1")
EXAMPLE_PATH = Path(__file__).with_name("gold_examples.json")
MODEL = "gpt-5.6-sol"
SAMPLE_SIZE = 250
SEED = 20260812
MAX_COMPLETION_TOKENS = 2048
RETRY_MAX_COMPLETION_TOKENS = 8192
MAX_ESTIMATED_INPUT_TOKENS = 250_000
HARD_COST_LIMIT_USD = 20.0
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
SOURCE_MARKER_RE = re.compile(r"^\s*Source\s+\[[^\]\r\n]+\](?:\s+https?://\S+)?\s*$", re.I)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temp.replace(path)


def _source_text(source_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    record = source_row["source_record"]
    publication = record.get("publication") or {}
    rendered = record.get("rendered_product") or {}
    body = rendered.get("text") if isinstance(rendered, Mapping) else None
    if not body:
        body = record.get("full_rendered_body") or record.get("normalized_text") or record.get("text") or ""
    title = str(publication.get("title") or record.get("title") or "").strip()
    provider = str(publication.get("provider") or record.get("provider") or "").strip()
    published = (
        publication.get("published_at_utc") or publication.get("published_at")
        or record.get("published_at_utc") or record.get("published_at") or ""
    )
    text = str(body)
    if title and title.casefold() not in text[: max(500, len(title) + 20)].casefold():
        text = f"Title: {title}\n{text}"
    return text, {"title": title, "provider": provider, "published_at_utc": str(published)}


def normalize_source(source_row: Mapping[str, Any], *, publication_fallback: str = "") -> dict[str, Any]:
    original, meta = _source_text(source_row)
    normalized = unicodedata.normalize("NFKC", html.unescape(original)).replace("\r\n", "\n").replace("\r", "\n")
    retained_lines = []
    for line in normalized.split("\n"):
        line = re.sub(r"[\t ]+", " ", line).strip()
        if line and not SOURCE_MARKER_RE.fullmatch(line):
            retained_lines.append(line)
    sentences: list[str] = []
    for line in retained_lines:
        sentences.extend(part.strip() for part in SENTENCE_SPLIT_RE.split(line) if part.strip())
    if not sentences:
        raise ValueError(f"source {source_row['source_id']} has no normalized sentences")
    sentence_rows = [{"sentence_id": i, "text": value} for i, value in enumerate(sentences, 1)]
    normalized_text = "\n".join(sentences)
    return {
        "source_id": str(source_row["source_id"]),
        "published_at_utc": meta["published_at_utc"] or publication_fallback,
        "normalized_sentences": sentence_rows,
        "metadata": {"title": meta["title"], "provider": meta["provider"]},
        "source_schema": source_row.get("source_schema"),
        "source_lineage": source_row.get("source_lineage"),
        "source_text_sha256": sha256_bytes(original.encode("utf-8")),
        "normalized_text_sha256": sha256_bytes(normalized_text.encode("utf-8")),
    }


def _validate_examples(
    bank: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]], gold: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for example in bank["examples"]:
        source_id = str(example["source_id"])
        if source_id not in sources or source_id not in gold:
            raise ValueError(f"example source absent from authority: {source_id}")
        sample = normalize_source(sources[source_id], publication_fallback=str(gold[source_id].get("source_timestamp") or ""))
        errors = validate_output(
            {"schema_version": SCHEMA_VERSION, "issuers": example["issuers"], "unresolved_issuer_mentions": example["unresolved_issuer_mentions"]},
            [row["sentence_id"] for row in sample["normalized_sentences"]],
        )
        if errors:
            raise ValueError(f"invalid example {source_id}: {errors}")
        gold_units = {normalize_ticker(row.get("ticker")): row for row in gold[source_id]["issuer_units"]}
        for issuer in example["issuers"]:
            ticker = normalize_ticker(issuer.get("ticker"))
            if ticker not in gold_units:
                raise ValueError(f"example ticker {ticker} absent from gold {source_id}")
            expected_eligible = gold_units[ticker]["forecast_eligibility"] == "eligible"
            if (issuer["forecast_relevance_probability"] >= 0.5) != expected_eligible:
                raise ValueError(f"example eligibility conflicts with gold: {source_id}/{ticker}")
        normalized[source_id] = sample
    return normalized


def _stratified_sample(rows: Sequence[Mapping[str, Any]], size: int, seed: int) -> list[Mapping[str, Any]]:
    groups: dict[tuple[str, bool], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["authority_id"]), bool(row["article_forecast_eligible"]))].append(row)
    rng = random.Random(seed)
    for values in groups.values():
        rng.shuffle(values)
    exact = {key: size * len(values) / len(rows) for key, values in groups.items()}
    allocation = {key: min(len(groups[key]), int(math.floor(value))) for key, value in exact.items()}
    remaining = size - sum(allocation.values())
    order = sorted(groups, key=lambda key: (-(exact[key] - allocation[key]), str(key)))
    while remaining:
        progressed = False
        for key in order:
            if allocation[key] < len(groups[key]):
                allocation[key] += 1
                remaining -= 1
                progressed = True
                if not remaining:
                    break
        if not progressed:
            raise ValueError("sample frame is too small")
    selected = [row for key in sorted(groups) for row in groups[key][: allocation[key]]]
    return sorted(selected, key=lambda row: str(row["source_id"]))


def _concept_tags(concepts: Sequence[Any]) -> list[str]:
    mapped: set[str] = set()
    for raw in concepts:
        value = str(raw).lower()
        rules = (
            ("acqui", "acquisition"), ("merger", "acquisition"), ("analyst", "analyst_action"),
            ("asset.sale", "asset_sale"), ("capital.return", "capital_return"), ("dividend", "capital_return"),
            ("buyback", "capital_return"), ("listing", "listing"), ("split", "capital_structure"),
            ("clinical", "clinical_trial"), ("contract", "commercial_contract"), ("earnings", "earnings"),
            ("financ", "financing"), ("liquidity", "financial_condition"), ("guidance", "guidance"),
            ("legal", "legal"), ("governance", "management_governance"), ("management", "management_governance"),
            ("market.", "market_observation"), ("operation", "operations"), ("ownership", "ownership"),
            ("partner", "partnership"), ("product", "product"), ("regulat", "regulatory"),
            ("solvency", "solvency"), ("strategy", "strategy"), ("workforce", "workforce"),
        )
        mapped.add(next((tag for marker, tag in rules if marker in value), "other_material"))
    return sorted(mapped)


def prepare(
    output_root: Path = DEFAULT_OUTPUT_ROOT, *, sample_size: int = SAMPLE_SIZE, seed: int = SEED
) -> dict[str, Any]:
    source_path = DEFAULT_AUDIT_ROOT / "audit_source_catalog.jsonl"
    gold_path = DEFAULT_GOLD_ROOT / "gold_labels.jsonl"
    sources_list = read_jsonl(source_path)
    gold_list = read_jsonl(gold_path)
    sources = {str(row["source_id"]): row for row in sources_list}
    gold = {str(row["source_id"]): row for row in gold_list}
    bank = load_example_bank(EXAMPLE_PATH)
    example_inputs = _validate_examples(bank, sources, gold)
    excluded_examples = example_source_ids(bank)
    common = sorted(set(sources) & set(gold) - excluded_examples)
    frame: list[dict[str, Any]] = []
    normalized_by_id: dict[str, dict[str, Any]] = {}
    context_excluded: list[str] = []
    system_prompt = build_system_prompt(bank, example_inputs)
    for source_id in common:
        sample = normalize_source(sources[source_id], publication_fallback=str(gold[source_id].get("source_timestamp") or ""))
        estimated = math.ceil((len(system_prompt) + len(json.dumps(sample, ensure_ascii=False))) / 3)
        if estimated > MAX_ESTIMATED_INPUT_TOKENS:
            context_excluded.append(source_id)
            continue
        normalized_by_id[source_id] = sample
        frame.append(gold[source_id])
    selected_gold = _stratified_sample(frame, sample_size, seed)
    samples = [normalized_by_id[str(row["source_id"])] for row in selected_gold]
    answer_key = []
    for row in selected_gold:
        answer_key.append({
            "source_id": row["source_id"],
            "article_forecast_eligible": bool(row["article_forecast_eligible"]),
            "authority_id": row["authority_id"],
            "issuer_units": [{
                "ticker": unit.get("ticker"),
                "forecast_eligibility": unit["forecast_eligibility"],
                "sentiment": unit["sentiment"],
                "concepts": unit.get("concepts") or [],
                "event_tags": _concept_tags(unit.get("concepts") or []),
                "tag_known": bool(unit.get("concepts")),
            } for unit in row["issuer_units"]],
        })
    requests_rows = []
    estimated_inputs = []
    for sample in samples:
        body = {
            "model": MODEL,
            "messages": build_messages(system_prompt, sample),
            "temperature": 0,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning_effort": "none",
            "response_format": {"type": "json_schema", "json_schema": {"name": SCHEMA_VERSION, "strict": True, "schema": TRANSPORT_SCHEMA}},
        }
        estimated_inputs.append(math.ceil(len(json.dumps(body, ensure_ascii=False)) / 3))
        requests_rows.append({"custom_id": sample["source_id"], "method": "POST", "url": "/v1/chat/completions", "body": body})
    output_root.mkdir(parents=True, exist_ok=True)
    sample_path = output_root / "sample.jsonl"
    answer_path = output_root / "answer_key.jsonl"
    batch_path = output_root / "batch_input.jsonl"
    write_jsonl(sample_path, samples)
    write_jsonl(answer_path, answer_key)
    write_jsonl(batch_path, requests_rows)
    input_tokens = sum(estimated_inputs)
    protected_cost = input_tokens / 1_000_000 * 2.50 + sample_size * MAX_COMPLETION_TOKENS / 1_000_000 * 15.0
    if protected_cost > HARD_COST_LIMIT_USD:
        raise ValueError(f"protected cost ${protected_cost:.2f} exceeds hard limit ${HARD_COST_LIMIT_USD:.2f}")
    strata = Counter((row["authority_id"], str(bool(row["article_forecast_eligible"]))) for row in selected_gold)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "model": MODEL,
        "sample_size": sample_size,
        "seed": seed,
        "sampling": "proportional stratified by authority_id and article_forecast_eligible; deterministic seeded shuffle",
        "example_count": len(excluded_examples),
        "example_source_ids_sha256": sha256_bytes("\n".join(sorted(excluded_examples)).encode()),
        "sample_example_overlap": sorted(excluded_examples & {row["source_id"] for row in samples}),
        "common_population": len(common),
        "context_ineligible_count": len(context_excluded),
        "strata": {f"{key[0]}|eligible={key[1]}": value for key, value in sorted(strata.items())},
        "estimated_input_tokens": input_tokens,
        "max_completion_tokens_per_request": MAX_COMPLETION_TOKENS,
        "protected_batch_cost_usd": round(protected_cost, 6),
        "hard_cost_limit_usd": HARD_COST_LIMIT_USD,
        "files": {
            "source_catalog": {"path": str(source_path), "sha256": sha256_file(source_path)},
            "gold_labels": {"path": str(gold_path), "sha256": sha256_file(gold_path)},
            "examples": {"path": str(EXAMPLE_PATH), "sha256": sha256_file(EXAMPLE_PATH)},
            "sample": {"path": str(sample_path), "sha256": sha256_file(sample_path)},
            "answer_key": {"path": str(answer_path), "sha256": sha256_file(answer_path)},
            "batch_input": {"path": str(batch_path), "sha256": sha256_file(batch_path), "bytes": batch_path.stat().st_size},
        },
    }
    if plan["sample_example_overlap"]:
        raise AssertionError("few-shot examples leaked into evaluation sample")
    write_json(output_root / "plan.json", plan)
    return plan


def _client() -> OpenAIClient:
    load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    return OpenAIClient(os.environ.get("OPENAI_API_KEY", ""), project_id=os.environ.get("OPENAI_PROJECT_ID", ""))


def submit(output_root: Path = DEFAULT_OUTPUT_ROOT, *, authorize_cost_usd: float) -> dict[str, Any]:
    plan = json.loads((output_root / "plan.json").read_text(encoding="utf-8"))
    protected = float(plan["protected_batch_cost_usd"])
    if authorize_cost_usd + 1e-9 < protected:
        raise ValueError(f"authorization ${authorize_cost_usd:.2f} is below protected cost ${protected:.2f}")
    state_path = output_root / "batch_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("batch_id"):
            return state
    client = _client()
    if MODEL not in client.model_ids():
        raise ValueError(f"model {MODEL} is not available to this project")
    upload = client.upload_batch_file(output_root / "batch_input.jsonl")
    batch = client.create_batch(str(upload["id"]), {"experiment": "llm_issuer_labeling_v3_gold_250", "sample_sha": plan["files"]["sample"]["sha256"][:16]})
    state = {"submitted_at_utc": utc_now(), "input_file_id": upload["id"], "batch_id": batch["id"], "status": batch["status"], "protected_cost_usd": protected}
    write_json(state_path, state)
    return state


def prepare_retry(
    output_root: Path = DEFAULT_OUTPUT_ROOT, *, max_completion_tokens: int = RETRY_MAX_COMPLETION_TOKENS
) -> dict[str, Any]:
    retry_root = output_root / "retry_01"
    previous_plan = json.loads((output_root / "plan.json").read_text(encoding="utf-8"))
    failures = read_jsonl(output_root / "failures.jsonl")
    retry_ids = sorted({str(row["source_id"]) for row in failures})
    if not retry_ids:
        return {"retry_count": 0, "retry_root": str(retry_root)}
    requests = {str(row["custom_id"]): row for row in read_jsonl(output_root / "batch_input.jsonl")}
    samples = {str(row["source_id"]): row for row in read_jsonl(output_root / "sample.jsonl")}
    answers = {str(row["source_id"]): row for row in read_jsonl(output_root / "answer_key.jsonl")}
    retry_requests = []
    for source_id in retry_ids:
        row = json.loads(json.dumps(requests[source_id]))
        row["body"]["max_completion_tokens"] = max_completion_tokens
        retry_requests.append(row)
    retry_samples = [samples[source_id] for source_id in retry_ids]
    retry_answers = [answers[source_id] for source_id in retry_ids]
    write_jsonl(retry_root / "batch_input.jsonl", retry_requests)
    write_jsonl(retry_root / "sample.jsonl", retry_samples)
    write_jsonl(retry_root / "answer_key.jsonl", retry_answers)
    estimated_input = sum(math.ceil(len(json.dumps(row["body"], ensure_ascii=False)) / 3) for row in retry_requests)
    protected = estimated_input / 1_000_000 * 2.50 + len(retry_ids) * max_completion_tokens / 1_000_000 * 15.0
    plan = {
        "created_at_utc": utc_now(), "model": MODEL, "sample_size": len(retry_ids),
        "retry_of": str(output_root),
        "reason": f"completion JSON truncated at the previous {previous_plan.get('max_completion_tokens_per_request', 'unknown')}-token cap",
        "estimated_input_tokens": estimated_input, "max_completion_tokens_per_request": max_completion_tokens,
        "protected_batch_cost_usd": round(protected, 6), "hard_cost_limit_usd": HARD_COST_LIMIT_USD,
        "files": {
            "sample": {"path": str(retry_root / "sample.jsonl"), "sha256": sha256_file(retry_root / "sample.jsonl")},
            "answer_key": {"path": str(retry_root / "answer_key.jsonl"), "sha256": sha256_file(retry_root / "answer_key.jsonl")},
            "batch_input": {"path": str(retry_root / "batch_input.jsonl"), "sha256": sha256_file(retry_root / "batch_input.jsonl"), "bytes": (retry_root / "batch_input.jsonl").stat().st_size},
        },
    }
    write_json(retry_root / "plan.json", plan)
    return plan


def merge_retry(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    retry_root = output_root / "retry_01"
    primary = {row["source_id"]: row for row in read_jsonl(output_root / "labels.jsonl")}
    retry = {row["source_id"]: row for row in read_jsonl(retry_root / "labels.jsonl")}
    primary.update(retry)
    expected = {row["source_id"] for row in read_jsonl(output_root / "sample.jsonl")}
    if set(primary) != expected:
        raise ValueError(f"merged labels do not cover sample: missing={sorted(expected-set(primary))} extra={sorted(set(primary)-expected)}")
    write_jsonl(output_root / "labels.jsonl", [primary[source_id] for source_id in sorted(primary)])
    write_jsonl(output_root / "failures.jsonl", [])
    result = evaluate(output_root)
    state_path = output_root / "batch_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"valid_label_count": len(primary), "failure_count": 0, "retry_batch_id": json.loads((retry_root / "batch_state.json").read_text(encoding="utf-8"))["batch_id"]})
    write_json(state_path, state)
    return result


def refresh(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    state_path = output_root / "batch_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    batch = _client().retrieve_batch(state["batch_id"])
    state.update({"checked_at_utc": utc_now(), "status": batch["status"], "output_file_id": batch.get("output_file_id"), "error_file_id": batch.get("error_file_id"), "request_counts": batch.get("request_counts")})
    write_json(state_path, state)
    return state


def cancel(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    state_path = output_root / "batch_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    batch = _client().cancel_batch(state["batch_id"])
    state.update({"cancel_requested_at_utc": utc_now(), "status": batch["status"]})
    archived = output_root / f"batch_state_superseded_{state['batch_id']}.json"
    write_json(archived, state)
    state_path.unlink()
    return state


def collect(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    state = refresh(output_root)
    if state["status"] not in TERMINAL_BATCH_STATUSES:
        return state
    client = _client()
    if state.get("output_file_id"):
        client.download_file(state["output_file_id"], output_root / "batch_output.jsonl")
    if state.get("error_file_id"):
        client.download_file(state["error_file_id"], output_root / "batch_errors.jsonl")
    return _persist_outputs(output_root, state)


def run_synchronous(output_root: Path, *, authorize_cost_usd: float) -> dict[str, Any]:
    plan = json.loads((output_root / "plan.json").read_text(encoding="utf-8"))
    batch_protected = float(plan["protected_batch_cost_usd"])
    protected = batch_protected * 2
    if authorize_cost_usd + 1e-9 < protected or protected > HARD_COST_LIMIT_USD:
        raise ValueError(f"authorization ${authorize_cost_usd:.2f} is below synchronous protected cost ${protected:.2f}")
    client = _client()
    outputs = []
    for row in read_jsonl(output_root / "batch_input.jsonl"):
        response = client.create_chat_completion(row["body"])
        outputs.append({"id": response.get("id"), "custom_id": row["custom_id"], "response": {"status_code": 200, "request_id": response.get("id"), "body": response}, "error": None})
    write_jsonl(output_root / "batch_output.jsonl", outputs)
    state = {"batch_id": "synchronous_recovery", "status": "completed", "submitted_at_utc": utc_now(), "completed_at_utc": utc_now(), "request_counts": {"total": len(outputs), "completed": len(outputs), "failed": 0}, "protected_cost_usd": protected}
    write_json(output_root / "batch_state.json", state)
    return _persist_outputs(output_root, state)


def _persist_outputs(output_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    samples = {row["source_id"]: row for row in read_jsonl(output_root / "sample.jsonl")}
    labels: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    output_path = output_root / "batch_output.jsonl"
    if output_path.exists():
        for row in read_jsonl(output_path):
            source_id = str(row.get("custom_id") or "")
            try:
                response = row["response"]
                if int(response["status_code"]) != 200:
                    raise ValueError(f"HTTP {response['status_code']}")
                message = response["body"]["choices"][0]["message"]
                payload = canonicalize_output(json.loads(message["content"]))
                errors = validate_output(payload, [s["sentence_id"] for s in samples[source_id]["normalized_sentences"]])
                if errors:
                    raise ValueError("; ".join(errors))
                labels.append({"source_id": source_id, "labels": payload, "usage": response["body"].get("usage")})
            except Exception as exc:
                failures.append({"source_id": source_id, "error": str(exc), "raw": row})
    expected = set(samples)
    returned = {row["source_id"] for row in labels} | {row["source_id"] for row in failures}
    for source_id in sorted(expected - returned):
        failures.append({"source_id": source_id, "error": "missing batch response"})
    write_jsonl(output_root / "labels.jsonl", sorted(labels, key=lambda row: row["source_id"]))
    write_jsonl(output_root / "failures.jsonl", sorted(failures, key=lambda row: row["source_id"]))
    state["valid_label_count"] = len(labels)
    state["failure_count"] = len(failures)
    write_json(output_root / "batch_state.json", state)
    if state["status"] == "completed":
        evaluate(output_root)
    return state


def _binary_metrics(gold: Sequence[bool], predicted: Sequence[bool]) -> dict[str, Any]:
    tp = sum(g and p for g, p in zip(gold, predicted)); tn = sum(not g and not p for g, p in zip(gold, predicted))
    fp = sum(not g and p for g, p in zip(gold, predicted)); fn = sum(g and not p for g, p in zip(gold, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0; recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {"n": len(gold), "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / len(gold) if gold else 0.0, "precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0, "balanced_accuracy": (recall + specificity) / 2}


def _sentiment(row: Mapping[str, Any]) -> str:
    positive = float(row["positive_implication_probability"]) >= 0.5
    negative = float(row["negative_implication_probability"]) >= 0.5
    return "mixed" if positive and negative else "positive" if positive else "negative" if negative else "neutral"


def evaluate(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    answers = {row["source_id"]: row for row in read_jsonl(output_root / "answer_key.jsonl")}
    labels = {row["source_id"]: row["labels"] for row in read_jsonl(output_root / "labels.jsonl")}
    article_gold: list[bool] = []; article_pred: list[bool] = []
    issuer_gold: list[bool] = []; issuer_pred: list[bool] = []; eligibility_probs: list[float] = []
    sentiments: list[tuple[str, str]] = []; tag_tp = tag_fp = tag_fn = tag_exact = tag_n = 0
    missing_gold_issuers = extra_predicted_issuers = eligible_extra_issuers = 0
    for source_id, answer in answers.items():
        payload = labels.get(source_id, {"issuers": []})
        predicted = {normalize_ticker(row.get("ticker")): row for row in payload.get("issuers", []) if normalize_ticker(row.get("ticker"))}
        gold_units = {normalize_ticker(row.get("ticker")): row for row in answer["issuer_units"] if normalize_ticker(row.get("ticker"))}
        article_gold.append(bool(answer["article_forecast_eligible"]))
        article_pred.append(any(float(row["forecast_relevance_probability"]) >= 0.5 for row in predicted.values()))
        for ticker, gold_row in gold_units.items():
            pred_row = predicted.get(ticker)
            is_eligible = gold_row["forecast_eligibility"] == "eligible"
            probability = float(pred_row["forecast_relevance_probability"]) if pred_row else 0.0
            issuer_gold.append(is_eligible); issuer_pred.append(probability >= 0.5); eligibility_probs.append(probability)
            if pred_row is None:
                missing_gold_issuers += 1
            if is_eligible:
                sentiments.append((gold_row["sentiment"], _sentiment(pred_row) if pred_row else "missing"))
            if gold_row["tag_known"]:
                tag_n += 1
                gold_tags = set(gold_row["event_tags"]); pred_tags = set(pred_row["event_tags"] if pred_row else [])
                tag_tp += len(gold_tags & pred_tags); tag_fp += len(pred_tags - gold_tags); tag_fn += len(gold_tags - pred_tags)
                tag_exact += gold_tags == pred_tags
        extras = set(predicted) - set(gold_units)
        extra_predicted_issuers += len(extras)
        eligible_extra_issuers += sum(float(predicted[t]["forecast_relevance_probability"]) >= 0.5 for t in extras)
    sentiment_labels = ("positive", "negative", "mixed", "neutral")
    confusion = {g: {p: 0 for p in (*sentiment_labels, "missing")} for g in sentiment_labels}
    for gold_value, pred_value in sentiments:
        confusion.setdefault(gold_value, {p: 0 for p in (*sentiment_labels, "missing")})[pred_value] += 1
    sentiment_accuracy = sum(g == p for g, p in sentiments) / len(sentiments) if sentiments else 0.0
    per_class_f1 = []
    for label in sentiment_labels:
        tp = sum(g == label and p == label for g, p in sentiments); fp = sum(g != label and p == label for g, p in sentiments); fn = sum(g == label and p != label for g, p in sentiments)
        precision = tp / (tp + fp) if tp + fp else 0.0; recall = tp / (tp + fn) if tp + fn else 0.0
        per_class_f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    tag_precision = tag_tp / (tag_tp + tag_fp) if tag_tp + tag_fp else 0.0; tag_recall = tag_tp / (tag_tp + tag_fn) if tag_tp + tag_fn else 0.0
    result = {
        "evaluated_at_utc": utc_now(), "threshold": 0.5, "sample_size": len(answers), "valid_output_count": len(labels),
        "article_forecast_eligibility": _binary_metrics(article_gold, article_pred),
        "issuer_forecast_eligibility": {**_binary_metrics(issuer_gold, issuer_pred), "brier_score": statistics.fmean((float(g) - p) ** 2 for g, p in zip(issuer_gold, eligibility_probs)) if issuer_gold else None},
        "eligible_issuer_sentiment": {"n": len(sentiments), "accuracy": sentiment_accuracy, "macro_f1": statistics.fmean(per_class_f1), "confusion": confusion},
        "event_tags_known_gold_units": {"n": tag_n, "exact_set_accuracy": tag_exact / tag_n if tag_n else None, "micro_precision": tag_precision, "micro_recall": tag_recall, "micro_f1": 2 * tag_precision * tag_recall / (tag_precision + tag_recall) if tag_precision + tag_recall else 0.0},
        "identity_coverage": {"missing_gold_issuer_rows": missing_gold_issuers, "extra_predicted_tickers": extra_predicted_issuers, "eligible_extra_predicted_tickers": eligible_extra_issuers},
        "not_scored": ["issuer_roles", "time_scope", "claim_source", "identity_confidence_probability because the consolidated gold has no uniform authority for these fields"],
    }
    write_json(output_root / "evaluation.json", result)
    report = ["# LLM Issuer Labeling V3 — Gold 250 Evaluation", "", f"Valid outputs: {len(labels)}/{len(answers)}", "", "| Metric | Accuracy | F1 / macro F1 | N |", "|---|---:|---:|---:|", f"| Article forecast eligibility | {result['article_forecast_eligibility']['accuracy']:.4f} | {result['article_forecast_eligibility']['f1']:.4f} | {result['article_forecast_eligibility']['n']} |", f"| Issuer forecast eligibility | {result['issuer_forecast_eligibility']['accuracy']:.4f} | {result['issuer_forecast_eligibility']['f1']:.4f} | {result['issuer_forecast_eligibility']['n']} |", f"| Eligible-issuer sentiment | {sentiment_accuracy:.4f} | {result['eligible_issuer_sentiment']['macro_f1']:.4f} | {len(sentiments)} |", f"| Event-tag exact set (known only) | {(result['event_tags_known_gold_units']['exact_set_accuracy'] or 0):.4f} | {result['event_tags_known_gold_units']['micro_f1']:.4f} | {tag_n} |", "", "Exact ticker identity matching is used. Missing predictions count as errors; extra predicted tickers are reported separately. Roles, time scope, claim source, and identity confidence are persisted but not scored because the consolidated gold does not uniformly certify them."]
    (output_root / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return result
