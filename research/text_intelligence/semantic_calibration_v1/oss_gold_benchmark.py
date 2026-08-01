from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib import error, request

import tiktoken

from .comparison import CollectionItem, evaluate_predictions, load_collection
from .candidate_contract import CANDIDATE_CONTRACT_VERSION, repair_item_candidates
from .openai_gold_benchmark import (
    PROMPT_VERSION,
    build_messages,
    output_token_budget,
    quality_score,
    response_schema,
    to_prediction,
    validate_response,
)
from .storage import assert_runtime_root, read_json, write_json_atomic


OSS_BENCHMARK_VERSION = "news_gold_oss_benchmark_v3"
OPENAI_BASELINE_VERSION = "news_gold_openai_benchmark_v6"


@dataclass(frozen=True, slots=True)
class OssProfile:
    name: str
    report_name: str
    model: str
    workers: int
    temperature: float
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None
    chat_template_kwargs: tuple[tuple[str, bool], ...] = ()
    server_args: tuple[str, ...] = ()


OSS_PROFILES: dict[str, OssProfile] = {
    "20b": OssProfile(
        "20b",
        "gpt-oss-20b",
        "openai/gpt-oss-20b",
        4,
        0.0,
        reasoning_effort="low",
        server_args=("--safetensors-load-strategy", "prefetch"),
    ),
    "120b": OssProfile(
        "120b",
        "gpt-oss-120b",
        "openai/gpt-oss-120b",
        4,
        0.0,
        reasoning_effort="low",
        server_args=("--safetensors-load-strategy", "prefetch"),
    ),
    "qwen35-a3b": OssProfile(
        "qwen35-a3b",
        "qwen3.5-35b-a3b",
        "Qwen/Qwen3.5-35B-A3B",
        4,
        0.7,
        top_p=0.8,
        top_k=20,
        presence_penalty=1.5,
        seed=42,
        chat_template_kwargs=(("enable_thinking", False),),
        server_args=(
            "--reasoning-parser",
            "qwen3",
            "--language-model-only",
            "--safetensors-load-strategy",
            "prefetch",
        ),
    ),
    "mistral-small-3.1-24b": OssProfile(
        "mistral-small-3.1-24b",
        "mistral-small-3.1-24b",
        "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        4,
        0.15,
        seed=42,
        server_args=(
            "--tokenizer-mode",
            "mistral",
            "--config-format",
            "mistral",
            "--load-format",
            "mistral",
        ),
    ),
}


class VllmHttpError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.retryable = status_code in {408, 409, 425, 429} or status_code >= 500
        super().__init__(f"vLLM HTTP {status_code}: {body[:1_000]}")


@dataclass(frozen=True, slots=True)
class OssBenchmarkConfig:
    shared_root: Path
    runtime_root: Path
    profile: str
    endpoint: str = "http://127.0.0.1:8000/v1/chat/completions"
    workers: int = 4
    timeout_seconds: int = 600
    attempts: int = 3
    min_output_tokens: int = 2_048
    max_output_tokens: int = 16_384
    max_model_len: int = 65_536

    @property
    def model_root(self) -> Path:
        return self.runtime_root / "models" / self.profile

    @property
    def bundle_path(self) -> Path:
        return self.shared_root / "gold_sample.jsonl"

    @property
    def bundle_manifest_path(self) -> Path:
        return self.shared_root / "bundle_manifest.json"

    @property
    def openai_baseline_path(self) -> Path:
        return self.shared_root / "openai_comparison_v6.json"


def prepare_bundle(
    *,
    collection_root: Path,
    selection_path: Path,
    openai_comparison_path: Path | None,
    shared_root: Path,
) -> dict[str, Any]:
    assert_runtime_root(shared_root)
    shared_root.mkdir(parents=True, exist_ok=True)
    selection = read_json(selection_path)
    if selection.get("benchmark_version") != OPENAI_BASELINE_VERSION:
        raise RuntimeError(
            f"Expected {OPENAI_BASELINE_VERSION} selection, got "
            f"{selection.get('benchmark_version')!r}."
        )
    sample_ids = [str(value) for value in selection.get("sample_ids") or ()]
    if len(sample_ids) != 100 or len(set(sample_ids)) != 100:
        raise RuntimeError("The OSS comparison requires the exact frozen 100-article sample.")
    by_id = {item.sample_id: item for item in load_collection(collection_root)}
    missing = sorted(set(sample_ids) - set(by_id))
    if missing:
        raise RuntimeError(f"Gold collection is missing frozen samples: {missing[:10]}")
    selected = tuple(
        repair_item_candidates(by_id[sample_id]) for sample_id in sample_ids
    )
    rows = [
        {
            "sample_id": item.sample_id,
            "split": item.split,
            "blinded": item.blinded,
            "truth": item.truth,
        }
        for item in selected
    ]
    bundle_path = shared_root / "gold_sample.jsonl"
    _write_or_validate_jsonl(bundle_path, rows)
    baseline_hash = ""
    if openai_comparison_path is not None and openai_comparison_path.exists():
        baseline = read_json(openai_comparison_path)
        if baseline.get("benchmark_version") != OPENAI_BASELINE_VERSION:
            raise RuntimeError("OpenAI comparison is not the certified V6 baseline.")
        if baseline.get("selection", {}).get("selection_sha256") != selection.get(
            "selection_sha256"
        ):
            raise RuntimeError("OpenAI comparison and frozen selection identities differ.")
        baseline_target = shared_root / "openai_comparison_v6.json"
        write_json_atomic(baseline_target, baseline)
        baseline_hash = _file_hash(baseline_target)
    manifest = {
        "benchmark_version": OSS_BENCHMARK_VERSION,
        "openai_baseline_version": OPENAI_BASELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
        "sample_rows": len(rows),
        "sample_ids": sample_ids,
        "selection_sha256": selection["selection_sha256"],
        "bundle_sha256": _file_hash(bundle_path),
        "openai_comparison_sha256": baseline_hash,
        "generated_at_utc": _utc_now(),
    }
    manifest_path = shared_root / "bundle_manifest.json"
    if manifest_path.exists():
        existing = read_json(manifest_path)
        for key in (
            "benchmark_version",
            "openai_baseline_version",
            "prompt_version",
            "candidate_contract_version",
            "sample_rows",
            "sample_ids",
            "selection_sha256",
            "bundle_sha256",
            "openai_comparison_sha256",
        ):
            if existing.get(key) != manifest.get(key):
                raise RuntimeError(f"Frozen OSS bundle manifest drifted at {key}.")
        manifest = existing
    else:
        write_json_atomic(manifest_path, manifest)
    return manifest


def load_bundle(config: OssBenchmarkConfig) -> tuple[CollectionItem, ...]:
    manifest = read_json(config.bundle_manifest_path)
    if manifest.get("benchmark_version") != OSS_BENCHMARK_VERSION:
        raise RuntimeError("OSS benchmark bundle version mismatch.")
    if _file_hash(config.bundle_path) != manifest.get("bundle_sha256"):
        raise RuntimeError("OSS benchmark bundle hash mismatch.")
    baseline_hash = str(manifest.get("openai_comparison_sha256") or "")
    if baseline_hash:
        if not config.openai_baseline_path.exists():
            raise RuntimeError("OSS benchmark manifest requires a missing OpenAI baseline.")
        if _file_hash(config.openai_baseline_path) != baseline_hash:
            raise RuntimeError("OpenAI baseline hash mismatch in OSS benchmark bundle.")
    rows = _read_jsonl(config.bundle_path)
    items = tuple(
        CollectionItem(
            sample_id=str(row["sample_id"]),
            split=str(row["split"]),
            blinded=dict(row["blinded"]),
            truth=dict(row["truth"]),
        )
        for row in rows
    )
    if len(items) != 100 or [item.sample_id for item in items] != manifest.get(
        "sample_ids"
    ):
        raise RuntimeError("OSS benchmark bundle population drifted.")
    return items


def run_profile(config: OssBenchmarkConfig, *, execute: bool) -> int:
    assert_runtime_root(config.runtime_root)
    profile = OSS_PROFILES[config.profile]
    items = load_bundle(config)
    _validate_request_capacity(config, items)
    print(
        f"OSS GOLD BENCHMARK | profile={profile.name} model={profile.model} "
        f"articles={len(items)} workers={config.workers} execute={execute}",
        flush=True,
    )
    print(
        f"CONTRACT | bundle_sha256={_file_hash(config.bundle_path)} "
        f"max_model_len={config.max_model_len:,}",
        flush=True,
    )
    if not execute:
        print("PLANNED | no vLLM request was made", flush=True)
        return 0

    config.model_root.mkdir(parents=True, exist_ok=True)
    served_models = check_server(config.endpoint, expected_model=profile.model)
    prediction_dir = config.model_root / "predictions"
    response_dir = config.model_root / "responses"
    failure_dir = config.model_root / "failures"
    completed_ids: set[str] = set()
    by_id = {item.sample_id: item for item in items}
    if prediction_dir.exists() or response_dir.exists():
        orphan_predictions = sorted(
            path.stem
            for path in prediction_dir.glob("*.json")
            if not (response_dir / path.name).exists()
        )
        if orphan_predictions:
            raise RuntimeError(
                "OSS predictions exist without authoritative responses: "
                + ", ".join(orphan_predictions[:10])
            )
        for path in response_dir.glob("*.json"):
            identifier = path.stem
            if identifier not in by_id:
                raise RuntimeError(f"Unknown existing OSS response identity: {identifier}")
            row = read_json(path)
            item = by_id[identifier]
            expected = {
                "benchmark_version": OSS_BENCHMARK_VERSION,
                "sample_id": identifier,
                "profile": profile.name,
                "model": profile.model,
                "prompt_version": PROMPT_VERSION,
                "candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
                "source_text_sha256": item.truth["source_text_sha256"],
                "annotation_sha256": item.truth["annotation_sha256"],
                "bundle_sha256": _file_hash(config.bundle_path),
            }
            for key, value in expected.items():
                if row.get(key) != value:
                    raise RuntimeError(
                        f"Existing OSS response contract drift at {identifier}.{key}."
                    )
            prediction_path = prediction_dir / f"{identifier}.json"
            if not prediction_path.exists():
                raise RuntimeError(
                    f"Existing OSS response lacks its prediction: {identifier}"
                )
            if read_json(prediction_path) != row.get("prediction"):
                raise RuntimeError(
                    f"Existing OSS response and prediction differ: {identifier}"
                )
            completed_ids.add(identifier)
    pending = [item for item in items if item.sample_id not in completed_ids]
    started = time.perf_counter()
    successes = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        futures = {
            pool.submit(infer_one, item, config, profile): item for item in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                result = future.result()
                write_json_atomic(response_dir / f"{item.sample_id}.json", result)
                write_json_atomic(
                    prediction_dir / f"{item.sample_id}.json", result["prediction"]
                )
                stale_failure = failure_dir / f"{item.sample_id}.json"
                if stale_failure.exists():
                    stale_failure.unlink()
                successes += 1
                status = "COMPLETED"
            except Exception as exc:  # keep each failed article resumable
                write_json_atomic(
                    failure_dir / f"{item.sample_id}.json",
                    {
                        "sample_id": item.sample_id,
                        "profile": profile.name,
                        "model": profile.model,
                        "error": str(exc)[:2_000],
                        "updated_at_utc": _utc_now(),
                    },
                )
                failures += 1
                status = "FAILED"
            elapsed = time.perf_counter() - started
            rate = index / elapsed if elapsed else 0.0
            eta = (len(pending) - index) / rate if rate else 0.0
            print(
                f"[{index:>3}/{len(pending)}] {status} {item.sample_id} "
                f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
                flush=True,
            )
    wall_seconds = time.perf_counter() - started
    segment = {
        "scheduled_rows": len(pending),
        "completed_rows": successes,
        "failed_rows": failures,
        "wall_seconds": round(wall_seconds, 6),
        "completed_at_utc": _utc_now(),
    }
    _append_jsonl(config.model_root / "segments.jsonl", segment)
    manifest = write_profile_outputs(config, items, served_models)
    write_combined_comparison(
        config.runtime_root,
        config.openai_baseline_path if config.openai_baseline_path.exists() else None,
    )
    print(
        f"DONE | valid={manifest['completed_rows']}/100 "
        f"quality={manifest['quality_score']:.4f} "
        f"report={config.runtime_root / 'COMPARISON_WITH_OPENAI.md'}",
        flush=True,
    )
    return 0 if manifest["completed_rows"] == len(items) else 2


def infer_one(
    item: CollectionItem,
    config: OssBenchmarkConfig,
    profile: OssProfile,
) -> dict[str, Any]:
    output_budget = output_token_budget(
        item,
        minimum=config.min_output_tokens,
        maximum=config.max_output_tokens,
    )
    base_messages = list(build_messages(item))
    messages = list(base_messages)
    maximum_retry_budget = _maximum_retry_output_budget(
        config, base_messages, output_budget
    )
    started = time.perf_counter()
    last_error: Exception | None = None
    attempts_used = 0
    for attempt in range(1, config.attempts + 1):
        attempts_used = attempt
        payload = _build_payload(profile, messages, output_budget)
        try:
            response = _post_json(config.endpoint, payload, config.timeout_seconds)
            choice = response["choices"][0]
            if str(choice.get("finish_reason") or "") == "length":
                last_error = RuntimeError("response_truncated_at_output_budget")
                if attempt < config.attempts and output_budget < maximum_retry_budget:
                    output_budget = min(maximum_retry_budget, output_budget * 2)
                    continue
                break
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(value.get("text") or "")
                    for value in content
                    if isinstance(value, Mapping)
                )
            try:
                value = json.loads(str(content))
            except (TypeError, ValueError) as exc:
                last_error = exc
                if attempt < config.attempts:
                    messages = _corrective_messages(
                        base_messages, "Return one complete JSON object matching the schema."
                    )
                    continue
                break
            validation_errors = validate_response(value, item)
            if validation_errors:
                last_error = RuntimeError(";".join(validation_errors))
                if attempt < config.attempts:
                    messages = _corrective_messages(
                        base_messages,
                        "Correct these contract violations: "
                        + "; ".join(validation_errors[:8]),
                    )
                    continue
                break
            elapsed = time.perf_counter() - started
            usage = response.get("usage") or {}
            completion_tokens = int(usage.get("completion_tokens") or 0)
            return {
                "benchmark_version": OSS_BENCHMARK_VERSION,
                "sample_id": item.sample_id,
                "profile": profile.name,
                "model": profile.model,
                "prompt_version": PROMPT_VERSION,
                "candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
                "source_text_sha256": item.truth["source_text_sha256"],
                "annotation_sha256": item.truth["annotation_sha256"],
                "bundle_sha256": _file_hash(config.bundle_path),
                "attempt": attempt,
                "elapsed_seconds": round(elapsed, 6),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": completion_tokens,
                "completion_tokens_per_second": round(
                    completion_tokens / elapsed if elapsed else 0.0, 6
                ),
                "output_token_budget": output_budget,
                "structured_output": value,
                "prediction": to_prediction(item, value, profile.report_name),
                "completed_at_utc": _utc_now(),
            }
        except (KeyError, IndexError, TypeError, VllmHttpError, OSError) as exc:
            last_error = exc
            if not isinstance(exc, OSError) and not (
                isinstance(exc, VllmHttpError) and exc.retryable
            ):
                break
            if attempt < config.attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(
        f"inference_failed_after_{attempts_used}_attempts:{last_error}"
    ) from last_error


def _build_payload(
    profile: OssProfile,
    messages: list[dict[str, Any]],
    output_budget: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "temperature": profile.temperature,
        "max_tokens": output_budget,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "news_gold_semantic_label",
                "strict": True,
                "schema": response_schema(),
            },
        },
    }
    optional = {
        "top_p": profile.top_p,
        "top_k": profile.top_k,
        "presence_penalty": profile.presence_penalty,
        "seed": profile.seed,
        "reasoning_effort": profile.reasoning_effort,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    if profile.chat_template_kwargs:
        payload["chat_template_kwargs"] = dict(profile.chat_template_kwargs)
    return payload


def _maximum_retry_output_budget(
    config: OssBenchmarkConfig,
    messages: list[dict[str, Any]],
    initial_budget: int,
) -> int:
    available = config.max_model_len - _harmony_prompt_tokens(messages) - 1_024
    return max(initial_budget, min(config.max_output_tokens, available))


def _corrective_messages(
    base_messages: list[dict[str, Any]], error_summary: str
) -> list[dict[str, Any]]:
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                "The previous response was rejected. "
                f"{error_summary} Regenerate the full answer, use only exact candidate "
                "canonical_instrument_id values, and include each candidate exactly once."
            ),
        },
    ]


def check_server(endpoint: str, *, expected_model: str) -> list[str]:
    models_url = endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        with request.urlopen(models_url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"vLLM is unavailable at {models_url}; start {expected_model} first."
        ) from exc
    models = sorted(
        str(row.get("id"))
        for row in payload.get("data") or ()
        if isinstance(row, Mapping) and row.get("id")
    )
    if expected_model not in models:
        raise RuntimeError(
            f"Expected served model {expected_model!r}; vLLM exposes {models}."
        )
    return models


def write_profile_outputs(
    config: OssBenchmarkConfig,
    items: tuple[CollectionItem, ...],
    served_models: list[str],
) -> dict[str, Any]:
    response_dir = config.model_root / "responses"
    prediction_dir = config.model_root / "predictions"
    responses = [
        read_json(response_dir / f"{item.sample_id}.json")
        for item in items
        if (response_dir / f"{item.sample_id}.json").exists()
        and (prediction_dir / f"{item.sample_id}.json").exists()
    ]
    metrics = evaluate_predictions(
        items,
        prediction_dir=prediction_dir,
        canonical_concepts=True,
        missing_as_failure=True,
    )
    score = quality_score(metrics)
    segments = _read_jsonl(config.model_root / "segments.jsonl")
    wall_seconds = sum(float(row.get("wall_seconds") or 0.0) for row in segments)
    request_seconds = sum(float(row.get("elapsed_seconds") or 0.0) for row in responses)
    prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in responses)
    completion_tokens = sum(
        int(row.get("completion_tokens") or 0) for row in responses
    )
    manifest = {
        "benchmark_version": OSS_BENCHMARK_VERSION,
        "profile": config.profile,
        "model": OSS_PROFILES[config.profile].model,
        "served_models": served_models,
        "sample_rows": len(items),
        "completed_rows": len(responses),
        "failure_rows": len(items) - len(responses),
        "quality_score": score,
        "workers": config.workers,
        "wall_seconds": round(wall_seconds, 6),
        "articles_per_minute": round(
            len(responses) / (wall_seconds / 60) if wall_seconds else 0.0, 6
        ),
        "aggregate_request_seconds": round(request_seconds, 6),
        "mean_request_seconds": round(
            request_seconds / len(responses) if responses else 0.0, 6
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "completion_tokens_per_second": round(
            completion_tokens / request_seconds if request_seconds else 0.0, 6
        ),
        "local_api_cost_usd": "0",
        "local_compute_cost_measured": False,
        "bundle_sha256": _file_hash(config.bundle_path),
        "generated_at_utc": _utc_now(),
    }
    write_json_atomic(config.model_root / "metrics.json", metrics)
    write_json_atomic(config.model_root / "manifest.json", manifest)
    return manifest


def write_combined_comparison(
    runtime_root: Path, baseline_path: Path | None
) -> Path:
    rows: list[dict[str, Any]] = []
    if baseline_path is not None:
        baseline = read_json(baseline_path)
        for name, result in baseline["models"].items():
            manifest = result["manifest"]
            rows.append(
                {
                    "model": name,
                    "mode": "OpenAI Batch",
                    "valid": int(manifest["completed_rows"]),
                    "quality": float(result["quality_score"]),
                    "direction_f1": float(result["metrics"]["semantic_direction"]["macro_f1"]),
                    "forecast_f1": float(
                        result["metrics"]["eligibility"]["forecast_trigger_eligible"]["f1"]
                    ),
                    "api_cost_usd": float(manifest["actual_batch_cost_usd"]),
                    "elapsed_seconds": float(manifest["batch_elapsed_seconds"]),
                    "articles_per_minute": float(manifest["articles_per_minute"]),
                    "completion_tokens_per_second": None,
                }
            )
    for profile_name, profile in OSS_PROFILES.items():
        model_root = runtime_root / "models" / profile_name
        manifest_path = model_root / "manifest.json"
        metrics_path = model_root / "metrics.json"
        if not manifest_path.exists() or not metrics_path.exists():
            continue
        manifest = read_json(manifest_path)
        metrics = read_json(metrics_path)
        rows.append(
            {
                "model": profile.report_name,
                "mode": "local vLLM",
                "valid": int(manifest["completed_rows"]),
                "quality": float(manifest["quality_score"]),
                "direction_f1": float(metrics["semantic_direction"]["macro_f1"]),
                "forecast_f1": float(
                    metrics["eligibility"]["forecast_trigger_eligible"]["f1"]
                ),
                "api_cost_usd": 0.0,
                "elapsed_seconds": float(manifest["wall_seconds"]),
                "articles_per_minute": float(manifest["articles_per_minute"]),
                "completion_tokens_per_second": float(
                    manifest["completion_tokens_per_second"]
                ),
            }
        )
    rows.sort(key=lambda row: (-row["quality"], row["api_cost_usd"]))
    payload = {
        "benchmark_version": OSS_BENCHMARK_VERSION,
        "openai_baseline_version": OPENAI_BASELINE_VERSION,
        "rows": rows,
        "generated_at_utc": _utc_now(),
    }
    write_json_atomic(runtime_root / "comparison_with_openai.json", payload)
    report_path = runtime_root / "COMPARISON_WITH_OPENAI.md"
    lines = [
        "# OpenAI and local vLLM gold-label comparison",
        "",
        "All rows use the same frozen 100 articles, prompt V3, typed canonical-instrument contract, validator, and all-100 scoring denominator.",
        (
            "The exact OpenAI V6 baseline is included."
            if baseline_path is not None
            else "The exact OpenAI V6 baseline is pending; only completed local V3 rows are shown."
        ),
        "OpenAI Batch elapsed time includes remote queueing. Local vLLM elapsed time is workstation wall time; the two are not latency-equivalent.",
        "Local API cost is zero, but GPU depreciation, electricity, and operator time were not metered and are not represented as zero compute cost.",
        "",
        "| Rank | Model | Mode | Valid | Quality | Direction F1 | Forecast F1 | API cost | Elapsed | Articles/min | Completion tok/s |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(rows, 1):
        token_rate = row["completion_tokens_per_second"]
        token_rate_text = f"{token_rate:.2f}" if token_rate is not None else "n/a"
        lines.append(
            f"| {rank} | {row['model']} | {row['mode']} | {row['valid']}/100 | "
            f"{row['quality']:.3f} | {row['direction_f1']:.3f} | "
            f"{row['forecast_f1']:.3f} | ${row['api_cost_usd']:.4f} | "
            f"{_duration(row['elapsed_seconds'])} | {row['articles_per_minute']:.2f} | "
            f"{token_rate_text} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _validate_request_capacity(
    config: OssBenchmarkConfig,
    items: Iterable[CollectionItem],
) -> None:
    failures: list[str] = []
    for item in items:
        output_tokens = output_token_budget(
            item,
            minimum=config.min_output_tokens,
            maximum=config.max_output_tokens,
        )
        prompt_tokens = _harmony_prompt_tokens(build_messages(item))
        if prompt_tokens + output_tokens + 1_024 > config.max_model_len:
            failures.append(
                f"{item.sample_id}:{prompt_tokens}+{output_tokens}+1024>"
                f"{config.max_model_len}"
            )
    if failures:
        raise RuntimeError(
            "Configured vLLM context cannot preserve the exact benchmark contract: "
            + ", ".join(failures[:10])
        )


def _post_json(url: str, payload: Mapping[str, Any], timeout: int) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    req = request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise VllmHttpError(exc.code, body) from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_or_validate_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise RuntimeError(f"Frozen JSONL drift: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _harmony_prompt_tokens(messages: Iterable[Mapping[str, Any]]) -> int:
    """Count GPT-OSS content with its o200k_harmony vocabulary.

    The fixed per-message allowance covers Harmony role/channel framing. The
    separate 1,024-token capacity reserve in the caller covers chat-template
    and structured-decoding overhead without treating UTF-8 bytes as tokens.
    """
    encoding = tiktoken.get_encoding("o200k_harmony")
    return sum(
        len(encoding.encode(str(message.get("content") or ""))) + 16
        for message in messages
    )


def _duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
