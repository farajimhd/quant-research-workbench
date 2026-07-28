from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from research.news_labeling.gpt_oss_v1.audit import write_audit
from research.news_labeling.gpt_oss_v1.data import read_jsonl, write_jsonl
from research.news_labeling.gpt_oss_v1.prompt import build_messages
from research.news_labeling.gpt_oss_v1.schema import (
    VLLM_TRANSPORT_SCHEMA,
    validate_label,
)
from research.news_labeling.gpt_oss_v1.taxonomy import LABEL_VERSION, PROMPT_VERSION

from .compare import write_multi_model_comparison
from .config import BatchConfig, EXPERIMENT_VERSION, MODEL_REGISTRY, RemoteModel
from .openai_api import OpenAIClient


TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
def run(
    config: BatchConfig,
    *,
    execute: bool,
    authorized_cost_usd: Decimal,
    no_wait: bool,
    api_key: str = "",
) -> int:
    sample = _load_and_validate_sample(config.sample_path)
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    plan = build_plan(config, sample)
    _write_or_validate_plan(config.plan_path, plan)
    _print_plan(plan, config)

    if not execute:
        print(
            "PLANNED | no API request was made; rerun with --execute and "
            "--authorize-cost-usd at least the protected total",
            flush=True,
        )
        return 0

    protected = Decimal(str(plan["protected_cost_usd"]))
    if protected > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Protected experiment cost ${protected:.6f} exceeds the hard maximum "
            f"${config.hard_max_cost_usd:.2f}; no API request was made."
        )
    if authorized_cost_usd < protected:
        raise RuntimeError(
            f"Explicit authorization ${authorized_cost_usd:.2f} is below protected "
            f"experiment cost ${protected:.6f}; no API request was made."
        )
    if authorized_cost_usd > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Authorization ${authorized_cost_usd:.2f} exceeds the script hard maximum "
            f"${config.hard_max_cost_usd:.2f}; lower the authorization."
        )

    client = OpenAIClient(
        api_key or os.environ.get("OPENAI_API_KEY", ""),
        project_id=config.project_id or os.environ.get("OPENAI_PROJECT_ID", ""),
        base_url=config.base_url,
    )
    available = client.model_ids()
    missing = sorted(
        MODEL_REGISTRY[profile].model
        for profile in config.profiles
        if MODEL_REGISTRY[profile].model not in available
    )
    if missing:
        raise RuntimeError(
            "The authenticated OpenAI project does not expose every requested model: "
            + ", ".join(missing)
            + ". No batch was submitted."
        )

    for profile in config.profiles:
        _submit_or_reconcile_model(
            client,
            config,
            MODEL_REGISTRY[profile],
            plan,
        )

    if no_wait:
        _print_status(config)
        print("SUBMITTED | rerun the same command later to reconcile results", flush=True)
        return 0

    while True:
        terminal = 0
        for profile in config.profiles:
            state = _refresh_and_collect(
                client,
                config,
                MODEL_REGISTRY[profile],
                sample,
                plan,
            )
            terminal += str(state.get("status") or "") in TERMINAL_BATCH_STATUSES
        _print_status(config)
        if terminal == len(config.profiles):
            break
        time.sleep(max(5, config.poll_seconds))

    report = write_multi_model_comparison(
        sample_path=config.sample_path,
        model_roots=[config.model_root(profile) for profile in config.profiles],
        output_root=config.comparison_root,
        disagreement_limit=config.disagreement_limit,
        answer_key_path=config.answer_key_path,
    )
    completed_models = sum(
        (config.model_root(profile) / "manifest.json").exists()
        for profile in config.profiles
    )
    print(
        f"COMPLETED | models={completed_models}/{len(config.profiles)} report={report}",
        flush=True,
    )
    return 0 if completed_models == len(config.profiles) else 2


def build_plan(config: BatchConfig, sample: list[dict[str, Any]]) -> dict[str, Any]:
    sample_hash = _file_sha256(config.sample_path)
    models: list[dict[str, Any]] = []
    total_cost = Decimal("0")
    for profile in config.profiles:
        if profile not in MODEL_REGISTRY:
            raise RuntimeError(f"Unpriced or unsupported model profile: {profile}")
        remote = MODEL_REGISTRY[profile]
        requests = [_batch_request(article, remote, config.max_output_tokens) for article in sample]
        input_path = config.model_root(profile) / "input.jsonl"
        _write_or_validate_jsonl(input_path, requests)
        input_tokens = sum(_conservative_input_tokens(row["body"]) for row in requests)
        output_tokens = len(requests) * config.max_output_tokens
        protected = _token_cost(remote, input_tokens, output_tokens)
        total_cost += protected
        models.append(
            {
                "profile": profile,
                "model": remote.model,
                "reasoning_effort": remote.reasoning_effort,
                "request_rows": len(requests),
                "estimated_input_tokens": input_tokens,
                "reserved_output_tokens": output_tokens,
                "batch_input_usd_per_million": str(
                    remote.batch_input_usd_per_million
                ),
                "batch_output_usd_per_million": str(
                    remote.batch_output_usd_per_million
                ),
                "protected_cost_usd": str(protected),
                "input_path": str(input_path),
                "input_sha256": _file_sha256(input_path),
            }
        )
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "label_version": LABEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "sample_path": str(config.sample_path),
        "sample_sha256": sample_hash,
        "sample_rows": len(sample),
        "max_output_tokens": config.max_output_tokens,
        "models": models,
        "protected_cost_usd": str(total_cost),
        "hard_max_cost_usd": str(config.hard_max_cost_usd),
        "generated_at_utc": _utc_now(),
    }


def _batch_request(
    article: dict[str, Any],
    remote: RemoteModel,
    max_output_tokens: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": remote.model,
        "messages": build_messages(article),
        "temperature": 0,
        "max_completion_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "news_semantic_label",
                "strict": True,
                "schema": VLLM_TRANSPORT_SCHEMA,
            },
        },
    }
    if remote.reasoning_effort:
        body["reasoning_effort"] = remote.reasoning_effort
    return {
        "custom_id": str(article["canonical_news_id"]),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }


def _submit_or_reconcile_model(
    client: OpenAIClient,
    config: BatchConfig,
    remote: RemoteModel,
    plan: dict[str, Any],
) -> dict[str, Any]:
    root = config.model_root(remote.profile)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    model_plan = _model_plan(plan, remote.profile)
    state = _read_json(state_path)
    _validate_state_contract(state, plan, model_plan)

    if not state.get("batch_id"):
        recovered = _find_remote_batch(
            client,
            experiment_version=EXPERIMENT_VERSION,
            profile=remote.profile,
            sample_sha256=str(plan["sample_sha256"]),
        )
        if recovered:
            state.update(_state_from_batch(recovered, plan, model_plan))
            _atomic_json(state_path, state)

    if not state.get("input_file_id"):
        uploaded = client.upload_batch_file(Path(str(model_plan["input_path"])))
        state.update(
            {
                "experiment_version": EXPERIMENT_VERSION,
                "profile": remote.profile,
                "model": remote.model,
                "sample_sha256": plan["sample_sha256"],
                "input_sha256": model_plan["input_sha256"],
                "input_file_id": str(uploaded["id"]),
                "status": "uploaded",
                "updated_at_utc": _utc_now(),
            }
        )
        _atomic_json(state_path, state)

    if not state.get("batch_id"):
        batch = client.create_batch(
            str(state["input_file_id"]),
            {
                "experiment": EXPERIMENT_VERSION,
                "profile": remote.profile,
                "sample_sha256": str(plan["sample_sha256"])[:32],
            },
        )
        state.update(_state_from_batch(batch, plan, model_plan))
        _atomic_json(state_path, state)
    return state


def _refresh_and_collect(
    client: OpenAIClient,
    config: BatchConfig,
    remote: RemoteModel,
    sample: list[dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, Any]:
    root = config.model_root(remote.profile)
    state_path = root / "state.json"
    state = _read_json(state_path)
    if not state.get("batch_id"):
        return state
    batch = client.retrieve_batch(str(state["batch_id"]))
    state.update(_state_from_batch(batch, plan, _model_plan(plan, remote.profile)))
    _atomic_json(state_path, state)

    output_file_id = str(state.get("output_file_id") or "")
    error_file_id = str(state.get("error_file_id") or "")
    if output_file_id:
        client.download_file(output_file_id, root / "raw_output.jsonl")
    if error_file_id:
        client.download_file(error_file_id, root / "raw_error.jsonl")
    if str(state.get("status") or "") in TERMINAL_BATCH_STATUSES:
        _materialize_model_result(config, remote, sample, state)
    return state


def _materialize_model_result(
    config: BatchConfig,
    remote: RemoteModel,
    sample: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    root = config.model_root(remote.profile)
    sample_by_id = {str(row["canonical_news_id"]): row for row in sample}
    labels: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in read_jsonl(root / "raw_output.jsonl"):
        identifier = str(row.get("custom_id") or "")
        article = sample_by_id.get(identifier)
        if article is None:
            failures.append(_failure(identifier, "identity_not_in_frozen_sample"))
            continue
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        if int(response.get("status_code") or 0) != 200:
            failures.append(
                _failure(identifier, f"HTTP {response.get('status_code')}: {row.get('error')}")
            )
            continue
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        try:
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text") or "")
                    for item in content
                    if isinstance(item, dict)
                )
            label = json.loads(str(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            failures.append(_failure(identifier, f"response_parse_error: {exc}"))
            continue
        supplied_text = "\n".join(
            (str(article.get("title") or ""), str(article.get("rendered_text") or ""))
        )
        validation_errors = validate_label(label, supplied_text)
        if validation_errors:
            failures.append(
                _failure(identifier, "validation_error: " + "; ".join(validation_errors))
            )
            continue
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        labels.append(
            {
                "label_version": LABEL_VERSION,
                "prompt_version": PROMPT_VERSION,
                "experiment_version": EXPERIMENT_VERSION,
                "model": remote.model,
                "canonical_news_id": identifier,
                "published_at_utc": article["published_at_utc"],
                "text_sha256": article["text_sha256"],
                "status": "completed",
                "label": label,
                "usage": {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                    "cached_prompt_tokens": int(
                        (usage.get("prompt_tokens_details") or {}).get(
                            "cached_tokens", 0
                        )
                    ),
                },
                "updated_at_utc": _utc_now(),
            }
        )

    known = {str(row["canonical_news_id"]) for row in labels + failures}
    for row in read_jsonl(root / "raw_error.jsonl"):
        identifier = str(row.get("custom_id") or "")
        if identifier and identifier not in known:
            failures.append(_failure(identifier, f"batch_error: {row.get('error')}"))
            known.add(identifier)
    for identifier in sample_by_id:
        if identifier not in known:
            failures.append(
                _failure(
                    identifier,
                    f"missing_batch_result: terminal_status={state.get('status')}",
                )
            )

    labels.sort(key=lambda row: str(row["canonical_news_id"]))
    failures.sort(key=lambda row: str(row["canonical_news_id"]))
    write_jsonl(root / "labels.jsonl", labels)
    write_jsonl(root / "failures.jsonl", failures)
    write_audit(
        root,
        sample,
        labels + failures,
        report_title=f"{remote.model} news semantic labeling audit",
    )
    prompt_tokens = sum(int(row["usage"]["prompt_tokens"]) for row in labels)
    completion_tokens = sum(int(row["usage"]["completion_tokens"]) for row in labels)
    conservative_cost = _token_cost(remote, prompt_tokens, completion_tokens)
    manifest = {
        "experiment_version": EXPERIMENT_VERSION,
        "label_version": LABEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "profile": remote.profile,
        "model": remote.model,
        "sample_rows": len(sample),
        "completed_rows": len(labels),
        "failed_rows": len(failures),
        "batch_id": state.get("batch_id"),
        "batch_status": state.get("status"),
        "batch_created_at": state.get("created_at"),
        "batch_completed_at": state.get("completed_at"),
        "batch_elapsed_seconds": _elapsed_seconds(
            state.get("created_at"), state.get("completed_at")
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_prompt_tokens": sum(
            int(row["usage"]["cached_prompt_tokens"]) for row in labels
        ),
        "conservative_actual_cost_usd": str(conservative_cost),
        "cost_note": (
            "Conservative upper bound prices all prompt tokens at uncached Batch input rate."
        ),
        "generated_at_utc": _utc_now(),
    }
    _atomic_json(root / "manifest.json", manifest)


def _state_from_batch(
    batch: dict[str, Any],
    plan: dict[str, Any],
    model_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "profile": model_plan["profile"],
        "model": model_plan["model"],
        "sample_sha256": plan["sample_sha256"],
        "input_sha256": model_plan["input_sha256"],
        "input_file_id": str(batch.get("input_file_id") or ""),
        "batch_id": str(batch.get("id") or ""),
        "status": str(batch.get("status") or ""),
        "output_file_id": str(batch.get("output_file_id") or ""),
        "error_file_id": str(batch.get("error_file_id") or ""),
        "request_counts": batch.get("request_counts") or {},
        "errors": batch.get("errors") or {},
        "created_at": batch.get("created_at"),
        "completed_at": batch.get("completed_at"),
        "updated_at_utc": _utc_now(),
    }


def _find_remote_batch(
    client: OpenAIClient,
    *,
    experiment_version: str,
    profile: str,
    sample_sha256: str,
) -> dict[str, Any] | None:
    expected_hash = sample_sha256[:32]
    candidates: list[dict[str, Any]] = []
    for batch in client.list_batches(limit=100):
        metadata = batch.get("metadata") if isinstance(batch.get("metadata"), dict) else {}
        if (
            metadata.get("experiment") == experiment_version
            and metadata.get("profile") == profile
            and metadata.get("sample_sha256") == expected_hash
        ):
            candidates.append(batch)
    if len(candidates) > 1:
        ids = ", ".join(str(row.get("id") or "") for row in candidates)
        raise RuntimeError(
            f"Multiple remote batches match {profile}/{expected_hash}: {ids}; "
            "refusing ambiguous reconciliation."
        )
    return candidates[0] if candidates else None


def _validate_state_contract(
    state: dict[str, Any],
    plan: dict[str, Any],
    model_plan: dict[str, Any],
) -> None:
    if not state:
        return
    expected = {
        "experiment_version": EXPERIMENT_VERSION,
        "profile": model_plan["profile"],
        "model": model_plan["model"],
        "sample_sha256": plan["sample_sha256"],
        "input_sha256": model_plan["input_sha256"],
    }
    drift = {
        key: (state.get(key), value)
        for key, value in expected.items()
        if state.get(key) not in (None, "", value)
    }
    if drift:
        raise RuntimeError(
            f"Durable Batch state drift for {model_plan['profile']}: {drift}. "
            "Use a new runtime root for a changed experiment."
        )


def _load_and_validate_sample(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if not rows:
        raise RuntimeError(f"Frozen comparison sample is empty or missing: {path}")
    required = {"canonical_news_id", "published_at_utc", "text_sha256", "rendered_text"}
    identities: set[str] = set()
    for index, row in enumerate(rows):
        missing = required - set(row)
        if missing:
            raise RuntimeError(f"Sample row {index} is missing {sorted(missing)}")
        identifier = str(row["canonical_news_id"])
        if identifier in identities:
            raise RuntimeError(f"Duplicate frozen identity: {identifier}")
        identities.add(identifier)
        digest = hashlib.sha256(str(row["rendered_text"]).encode("utf-8")).hexdigest()
        if digest != row["text_sha256"]:
            raise RuntimeError(f"Rendered text hash drift for frozen identity {identifier}")
    return rows


def _model_plan(plan: dict[str, Any], profile: str) -> dict[str, Any]:
    for row in plan["models"]:
        if row["profile"] == profile:
            return row
    raise KeyError(profile)


def _token_cost(remote: RemoteModel, input_tokens: int, output_tokens: int) -> Decimal:
    million = Decimal("1000000")
    return (
        Decimal(input_tokens) * remote.batch_input_usd_per_million
        + Decimal(output_tokens) * remote.batch_output_usd_per_million
    ) / million


def _conservative_input_tokens(body: dict[str, Any]) -> int:
    encoded = json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    # Financial text and JSON keys are token dense. Three UTF-8 bytes/token is
    # deliberately more conservative than the usual prose approximation.
    return max(1, math.ceil(len(encoded) / 3))


def _print_plan(plan: dict[str, Any], config: BatchConfig) -> None:
    print(
        f"OPENAI NEWS LABEL BATCH V1 | sample={plan['sample_rows']:,} "
        f"models={len(plan['models'])} execute_plan=${Decimal(plan['protected_cost_usd']):.4f} "
        f"hard_max=${config.hard_max_cost_usd:.2f}",
        flush=True,
    )
    for row in plan["models"]:
        print(
            f"PLAN {row['profile']} rows={row['request_rows']:,} "
            f"input~={row['estimated_input_tokens']:,} "
            f"output_reserved={row['reserved_output_tokens']:,} "
            f"protected=${Decimal(row['protected_cost_usd']):.4f}",
            flush=True,
        )


def _print_status(config: BatchConfig) -> None:
    for profile in config.profiles:
        state = _read_json(config.model_root(profile) / "state.json")
        counts = state.get("request_counts") or {}
        print(
            f"STATUS {profile} {state.get('status') or 'not_submitted'} "
            f"completed={counts.get('completed', 0)} failed={counts.get('failed', 0)} "
            f"total={counts.get('total', 0)} batch={state.get('batch_id') or '-'}",
            flush=True,
        )


def _failure(identifier: str, error: str) -> dict[str, Any]:
    return {
        "label_version": LABEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "canonical_news_id": identifier,
        "status": "failed",
        "error": error[:2_000],
        "updated_at_utc": _utc_now(),
    }


def _write_or_validate_plan(path: Path, plan: dict[str, Any]) -> None:
    existing = _read_json(path)
    if existing:
        stable_keys = (
            "experiment_version",
            "label_version",
            "prompt_version",
            "sample_sha256",
            "sample_rows",
            "max_output_tokens",
            "models",
            "protected_cost_usd",
            "hard_max_cost_usd",
        )
        drift = [key for key in stable_keys if existing.get(key) != plan.get(key)]
        if drift:
            raise RuntimeError(
                f"Existing Batch plan drift in {drift}; use a new runtime root."
            )
        return
    _atomic_json(path, plan)


def _write_or_validate_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    serialized = [
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        for row in rows
    ]
    expected = ("\n".join(serialized) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != expected:
            raise RuntimeError(f"Existing Batch input drift: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(expected)
    temporary.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _elapsed_seconds(start: Any, end: Any) -> int:
    try:
        return max(0, int(end) - int(start))
    except (TypeError, ValueError):
        return 0
