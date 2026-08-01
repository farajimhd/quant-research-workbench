from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.news_labeling.openai_batch_v1.openai_api import OpenAIClient

from .comparison import (
    CANONICAL_CONCEPT_FAMILIES,
    CollectionItem,
    evaluate_predictions,
    load_collection,
)
from .schema import CONTENT_ROLES, DIRECTIONS, EXTRACTION_DECISIONS, SOURCE_ORIGINS
from .storage import assert_runtime_root, read_json, write_json_atomic


BENCHMARK_VERSION = "news_gold_openai_benchmark_v4"
# Preserve the exact V2 sample while request and scoring contracts evolve.
SELECTION_VERSION = "news_gold_openai_benchmark_v2"
PROMPT_VERSION = "news_gold_teacher_prompt_v1"
HARD_MAX_COST_USD = Decimal("20.00")
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    model: str
    batch_input_usd_per_million: Decimal
    batch_output_usd_per_million: Decimal
    reasoning_effort: str | None = "none"


MODEL_PROFILES: dict[str, ModelProfile] = {
    "gpt-5.6-sol": ModelProfile(
        "gpt-5.6-sol", "gpt-5.6-sol", Decimal("2.50"), Decimal("15.00")
    ),
    "gpt-5.6-terra": ModelProfile(
        "gpt-5.6-terra", "gpt-5.6-terra", Decimal("1.25"), Decimal("7.50")
    ),
    "gpt-5.6-luna": ModelProfile(
        "gpt-5.6-luna", "gpt-5.6-luna", Decimal("0.50"), Decimal("3.00")
    ),
    "gpt-5.4-mini": ModelProfile(
        "gpt-5.4-mini", "gpt-5.4-mini", Decimal("0.375"), Decimal("2.25")
    ),
    "gpt-5.4-nano": ModelProfile(
        "gpt-5.4-nano", "gpt-5.4-nano", Decimal("0.10"), Decimal("0.625")
    ),
    "gpt-4.1-mini": ModelProfile(
        "gpt-4.1-mini", "gpt-4.1-mini", Decimal("0.20"), Decimal("0.80"), None
    ),
    "gpt-4.1-nano": ModelProfile(
        "gpt-4.1-nano", "gpt-4.1-nano", Decimal("0.05"), Decimal("0.20"), None
    ),
}


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    collection_root: Path
    runtime_root: Path
    profiles: tuple[str, ...] = tuple(MODEL_PROFILES)
    sample_size: int = 100
    max_output_tokens: int = 2_048
    max_dynamic_output_tokens: int = 16_384
    poll_seconds: int = 30
    hard_max_cost_usd: Decimal = HARD_MAX_COST_USD
    base_url: str = "https://api.openai.com/v1"
    project_id: str = ""

    def model_root(self, profile: str) -> Path:
        return self.runtime_root / "models" / profile


def run_benchmark(
    config: BenchmarkConfig,
    *,
    execute: bool,
    authorized_cost_usd: Decimal,
    no_wait: bool,
) -> int:
    assert_runtime_root(config.runtime_root)
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    items = load_collection(config.collection_root)
    selected = prepare_selection(config, items)
    plan = build_plan(config, selected)
    _write_or_validate_plan(config.runtime_root / "plan.json", plan)
    print_plan(plan)
    if not execute:
        print("PLANNED | no OpenAI request was made", flush=True)
        return 0

    protected = Decimal(str(plan["protected_cost_usd"]))
    if protected > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Protected cost ${protected:.6f} exceeds hard maximum "
            f"${config.hard_max_cost_usd:.2f}."
        )
    if authorized_cost_usd < protected:
        raise RuntimeError(
            f"Authorization ${authorized_cost_usd:.2f} is below protected cost "
            f"${protected:.6f}."
        )
    if authorized_cost_usd > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Authorization ${authorized_cost_usd:.2f} exceeds hard maximum "
            f"${config.hard_max_cost_usd:.2f}."
        )
    client = OpenAIClient(
        os.environ.get("OPENAI_API_KEY", ""),
        project_id=config.project_id,
        base_url=config.base_url,
        timeout_seconds=180,
    )
    available = client.model_ids()
    missing = [
        MODEL_PROFILES[name].model
        for name in config.profiles
        if MODEL_PROFILES[name].model not in available
    ]
    if missing:
        raise RuntimeError(
            "Authenticated project does not expose requested models: "
            + ", ".join(missing)
        )
    for profile in config.profiles:
        submit_or_reconcile(client, config, MODEL_PROFILES[profile], plan)
    if no_wait:
        print_status(config)
        print("SUBMITTED | rerun without --no-wait to collect", flush=True)
        return 0

    while True:
        terminal = 0
        for profile in config.profiles:
            state = refresh_and_collect(
                client,
                config,
                MODEL_PROFILES[profile],
                selected,
                plan,
            )
            terminal += str(state.get("status") or "") in TERMINAL_BATCH_STATUSES
        print_status(config)
        if terminal == len(config.profiles):
            break
        time.sleep(max(5, config.poll_seconds))
    report = write_comparison(config, selected)
    print(f"COMPLETED | report={report}", flush=True)
    return 0


def prepare_selection(
    config: BenchmarkConfig,
    items: tuple[CollectionItem, ...],
) -> tuple[CollectionItem, ...]:
    selection_path = config.runtime_root / "selection.json"
    if selection_path.exists():
        payload = read_json(selection_path)
        identifiers = [str(value) for value in payload.get("sample_ids") or ()]
        by_id = {item.sample_id: item for item in items}
        if len(identifiers) != config.sample_size or len(set(identifiers)) != len(identifiers):
            raise RuntimeError("Frozen benchmark selection has invalid size or duplicates.")
        if any(identifier not in by_id for identifier in identifiers):
            raise RuntimeError("Frozen benchmark selection references an unknown article.")
        selected = tuple(by_id[identifier] for identifier in identifiers)
        if payload.get("selection_sha256") != _selection_hash(selected):
            raise RuntimeError("Frozen benchmark selection drifted from gold annotations.")
        return selected

    buckets: dict[tuple[Any, ...], list[CollectionItem]] = defaultdict(list)
    for item in items:
        buckets[_selection_signature(item)].append(item)
    for values in buckets.values():
        values.sort(key=lambda item: _stable_order(item.sample_id))
    selected: list[CollectionItem] = []
    bucket_order = sorted(buckets, key=lambda key: _stable_order(json.dumps(key)))
    depth = 0
    while len(selected) < config.sample_size:
        added = False
        for key in bucket_order:
            values = buckets[key]
            if depth < len(values):
                selected.append(values[depth])
                added = True
                if len(selected) == config.sample_size:
                    break
        if not added:
            break
        depth += 1
    if len(selected) != config.sample_size:
        raise RuntimeError(
            f"Could select only {len(selected)} of {config.sample_size} requested articles."
        )
    selected_tuple = tuple(selected)
    payload = {
        "benchmark_version": BENCHMARK_VERSION,
        "method": "deterministic_round_robin_over_gold_semantic_strata_v1",
        "sample_size": config.sample_size,
        "sample_ids": [item.sample_id for item in selected_tuple],
        "selection_sha256": _selection_hash(selected_tuple),
        "distribution": selection_distribution(selected_tuple),
    }
    write_json_atomic(selection_path, payload)
    return selected_tuple


def selection_distribution(items: Iterable[CollectionItem]) -> dict[str, Any]:
    rows = tuple(items)
    return {
        "extraction_decision": dict(Counter(item.truth["extraction_decision"] for item in rows)),
        "content_role": dict(Counter(item.truth["content_role"] for item in rows)),
        "source_origin": dict(Counter(item.truth["source_origin"] for item in rows)),
        "issuer_count": dict(Counter(str(len(item.truth.get("issuer_units") or ())) for item in rows)),
        "semantic_direction": dict(Counter(
            str(unit["semantic_direction"])
            for item in rows
            for unit in item.truth.get("issuer_units") or ()
        )),
        "forecast_eligible_units": sum(
            bool(unit.get("forecast_trigger_eligible"))
            for item in rows
            for unit in item.truth.get("issuer_units") or ()
        ),
    }


def build_plan(
    config: BenchmarkConfig,
    selected: tuple[CollectionItem, ...],
) -> dict[str, Any]:
    total = Decimal("0")
    models: list[dict[str, Any]] = []
    for profile_name in config.profiles:
        profile = MODEL_PROFILES[profile_name]
        requests = [
            batch_request(
                item,
                profile,
                output_token_budget(
                    item,
                    minimum=config.max_output_tokens,
                    maximum=config.max_dynamic_output_tokens,
                ),
            )
            for item in selected
        ]
        input_path = config.model_root(profile_name) / "input.jsonl"
        _write_or_validate_jsonl(input_path, requests)
        estimated_input_tokens = sum(
            _conservative_tokens(json.dumps(row["body"], ensure_ascii=False))
            for row in requests
        )
        reserved_output_tokens = sum(
            int(row["body"]["max_completion_tokens"])
            for row in requests
        )
        protected = _cost(
            profile,
            estimated_input_tokens,
            reserved_output_tokens,
        )
        total += protected
        models.append(
            {
                "name": profile.name,
                "model": profile.model,
                "batch_input_usd_per_million": str(profile.batch_input_usd_per_million),
                "batch_output_usd_per_million": str(profile.batch_output_usd_per_million),
                "reasoning_effort": profile.reasoning_effort,
                "request_rows": len(requests),
                "estimated_input_tokens": estimated_input_tokens,
                "reserved_output_tokens": reserved_output_tokens,
                "protected_cost_usd": str(protected),
                "input_path": str(input_path),
                "input_sha256": _file_hash(input_path),
            }
        )
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "prompt_version": PROMPT_VERSION,
        "selection_sha256": _selection_hash(selected),
        "sample_size": len(selected),
        "max_output_tokens": config.max_output_tokens,
        "max_dynamic_output_tokens": config.max_dynamic_output_tokens,
        "models": models,
        "protected_cost_usd": str(total),
        "hard_max_cost_usd": str(config.hard_max_cost_usd),
    }


def batch_request(
    item: CollectionItem,
    profile: ModelProfile,
    max_output_tokens: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": build_messages(item),
        "temperature": 0,
        "max_completion_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "news_gold_semantic_label",
                "strict": True,
                "schema": response_schema(),
            },
        },
    }
    if profile.reasoning_effort:
        body["reasoning_effort"] = profile.reasoning_effort
    return {
        "custom_id": item.sample_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }


def build_messages(item: CollectionItem) -> list[dict[str, str]]:
    publication = item.blinded["publication"]
    candidates = item.blinded.get("point_in_time_issuer_candidates") or []
    candidate_lines = []
    for candidate in candidates:
        aliases = [
            str(value).split(":", 1)[1]
            for value in candidate.get("identity_evidence") or ()
            if str(value).startswith("issuer_alias:")
        ]
        candidate_lines.append(
            f"- {candidate.get('ticker')}: aliases={', '.join(aliases) or 'none'}"
        )
    user = "\n".join(
        (
            f"Title: {publication.get('title') or ''}",
            f"Teaser: {publication.get('teaser') or ''}",
            f"Author: {publication.get('author') or ''}",
            f"Provider tags: {', '.join(publication.get('provider_tags') or ()) or 'none'}",
            f"Channels: {', '.join(publication.get('channels') or ()) or 'none'}",
            f"Provider tickers: {', '.join(publication.get('provider_tickers') or ()) or 'none'}",
            "Point-in-time issuer candidates:",
            *(candidate_lines or ["- none"]),
            "",
            "Certified rendered article:",
            str(item.blinded["rendered_product"].get("text") or ""),
        )
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


SYSTEM_PROMPT = """You label the semantic meaning of financial news using only the supplied text and metadata.
Do not use remembered market prices, later events, or subsequent price reactions.

Article fields:
- extraction_decision=labeled only when the article supports at least one issuer-specific semantic unit.
- content_role: primary_event is new issuer news; regulatory_event is a primary regulatory disclosure; analyst_event is an analyst rating/target/opinion; editorial_analysis is original analysis; automated_summary is generated summary; market_roundup is broad market coverage; mover_recap reports lists of movers; why_moving_followup explains an already-observed move; preview discusses an anticipated future event.
- source_origin identifies who originated the information, not the website that republished it.

Issuer units:
- Return only bare ticker symbols genuinely supported by issuer-specific evidence. A ticker list, comparison, incidental mention, or chart illustration is not enough.
- semantic_direction describes the text's issuer-specific financial implication: positive, negative, neutral, or mixed. It is not subsequent price direction.
- event_families use only the supplied closed family names.
- forecast_trigger_eligible is true only for timely issuer-specific information that could reasonably initiate a new forecast. Analyst opinions, recaps, roundups, previews, and why-moving follow-ups are normally false.
- reaction_evaluation_eligible is true only when a subsequent market reaction can be causally evaluated against this new event.
- issuer_history_context_eligible is true when the item is useful causal issuer history even if it should not trigger a new forecast.

If extraction_decision is not labeled, issuer_units must be empty. If it is labeled, issuer_units must be non-empty. Do not invent tickers or facts."""


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "extraction_decision",
            "content_role",
            "source_origin",
            "issuer_units",
        ],
        "properties": {
            "extraction_decision": {"type": "string", "enum": sorted(EXTRACTION_DECISIONS)},
            "content_role": {"type": "string", "enum": sorted(CONTENT_ROLES)},
            "source_origin": {"type": "string", "enum": sorted(SOURCE_ORIGINS)},
            "issuer_units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "ticker",
                        "semantic_direction",
                        "event_families",
                        "forecast_trigger_eligible",
                        "reaction_evaluation_eligible",
                        "issuer_history_context_eligible",
                    ],
                    "properties": {
                        "ticker": {"type": "string"},
                        "semantic_direction": {"type": "string", "enum": sorted(DIRECTIONS)},
                        "event_families": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(CANONICAL_CONCEPT_FAMILIES)},
                        },
                        "forecast_trigger_eligible": {"type": "boolean"},
                        "reaction_evaluation_eligible": {"type": "boolean"},
                        "issuer_history_context_eligible": {"type": "boolean"},
                    },
                },
            },
        },
    }


def validate_response(value: Mapping[str, Any], item: CollectionItem) -> list[str]:
    errors: list[str] = []
    if value.get("extraction_decision") not in EXTRACTION_DECISIONS:
        errors.append("invalid_extraction_decision")
    if value.get("content_role") not in CONTENT_ROLES:
        errors.append("invalid_content_role")
    if value.get("source_origin") not in SOURCE_ORIGINS:
        errors.append("invalid_source_origin")
    units = value.get("issuer_units")
    if not isinstance(units, list):
        return errors + ["issuer_units_not_list"]
    if value.get("extraction_decision") == "labeled" and not units:
        errors.append("labeled_without_units")
    if value.get("extraction_decision") != "labeled" and units:
        errors.append("abstention_with_units")
    allowed = {
        str(candidate.get("ticker") or "").upper()
        for candidate in item.blinded.get("point_in_time_issuer_candidates") or ()
    }
    allowed.update(
        str(ticker).upper()
        for ticker in item.blinded["publication"].get("provider_tickers") or ()
    )
    seen: set[str] = set()
    for unit in units:
        if not isinstance(unit, Mapping):
            errors.append("unit_not_object")
            continue
        ticker = str(unit.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            errors.append("invalid_or_duplicate_ticker")
        seen.add(ticker)
        if ticker not in allowed:
            errors.append(f"ticker_outside_candidates:{ticker}")
        if unit.get("semantic_direction") not in DIRECTIONS:
            errors.append(f"invalid_direction:{ticker}")
        families = unit.get("event_families")
        if not isinstance(families, list) or any(
            family not in CANONICAL_CONCEPT_FAMILIES for family in families
        ):
            errors.append(f"invalid_event_families:{ticker}")
        for field in (
            "forecast_trigger_eligible",
            "reaction_evaluation_eligible",
            "issuer_history_context_eligible",
        ):
            if not isinstance(unit.get(field), bool):
                errors.append(f"invalid_{field}:{ticker}")
    return errors


def to_prediction(item: CollectionItem, value: Mapping[str, Any], profile: str) -> dict[str, Any]:
    labels = [
        {
            "ticker": str(unit["ticker"]).upper(),
            "classification": {
                "content_role": value["content_role"],
                "source_origin": value["source_origin"],
                "event_concepts": list(unit["event_families"]),
                "semantic_direction": unit["semantic_direction"],
            },
            "forecast_trigger_eligible": unit["forecast_trigger_eligible"],
            "reaction_evaluation_eligible": unit["reaction_evaluation_eligible"],
            "issuer_history_context_eligible": unit["issuer_history_context_eligible"],
        }
        for unit in value["issuer_units"]
    ]
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "prompt_version": PROMPT_VERSION,
        "profile": profile,
        "sample_id": item.sample_id,
        "source_id": item.blinded["source_id"],
        "extraction_decision": value["extraction_decision"],
        "content_role": value["content_role"],
        "source_origin": value["source_origin"],
        "labels": labels,
    }


def submit_or_reconcile(
    client: OpenAIClient,
    config: BenchmarkConfig,
    profile: ModelProfile,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    root = config.model_root(profile.name)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    state = read_json(state_path) if state_path.exists() else {}
    model_plan = _model_plan(plan, profile.name)
    _validate_state(state, plan, model_plan)
    if not state.get("batch_id"):
        recovered = _find_remote_batch(client, profile.name, str(plan["selection_sha256"]))
        if recovered:
            state.update(_state_from_batch(recovered, plan, model_plan))
            write_json_atomic(state_path, state)
    if not state.get("input_file_id"):
        uploaded = client.upload_batch_file(Path(str(model_plan["input_path"])))
        state.update(
            {
                "benchmark_version": BENCHMARK_VERSION,
                "profile": profile.name,
                "model": profile.model,
                "selection_sha256": plan["selection_sha256"],
                "input_sha256": model_plan["input_sha256"],
                "input_file_id": str(uploaded["id"]),
                "status": "uploaded",
            }
        )
        write_json_atomic(state_path, state)
    if not state.get("batch_id"):
        batch = client.create_batch(
            str(state["input_file_id"]),
            {
                "experiment": BENCHMARK_VERSION,
                "profile": profile.name,
                "selection": str(plan["selection_sha256"])[:32],
            },
        )
        state.update(_state_from_batch(batch, plan, model_plan))
        write_json_atomic(state_path, state)
    return state


def refresh_and_collect(
    client: OpenAIClient,
    config: BenchmarkConfig,
    profile: ModelProfile,
    selected: tuple[CollectionItem, ...],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    root = config.model_root(profile.name)
    state_path = root / "state.json"
    state = read_json(state_path)
    batch = client.retrieve_batch(str(state["batch_id"]))
    state.update(_state_from_batch(batch, plan, _model_plan(plan, profile.name)))
    write_json_atomic(state_path, state)
    if state.get("status") not in TERMINAL_BATCH_STATUSES:
        return state
    output_path = root / "output.jsonl"
    if state.get("output_file_id") and not output_path.exists():
        client.download_file(str(state["output_file_id"]), output_path)
    error_path = root / "error.jsonl"
    if state.get("error_file_id") and not error_path.exists():
        client.download_file(str(state["error_file_id"]), error_path)
    if not (root / "manifest.json").exists():
        collect_model(
            config,
            profile,
            selected,
            state,
            output_path if output_path.exists() else None,
            error_path if error_path.exists() else None,
        )
    return state


def collect_model(
    config: BenchmarkConfig,
    profile: ModelProfile,
    selected: tuple[CollectionItem, ...],
    state: Mapping[str, Any],
    output_path: Path | None,
    error_path: Path | None,
) -> None:
    by_id = {item.sample_id: item for item in selected}
    prediction_dir = config.model_root(profile.name) / "predictions"
    failures: list[dict[str, Any]] = []
    prompt_tokens = 0
    completion_tokens = 0
    seen: set[str] = set()
    completed = 0
    for row in (_read_jsonl(output_path) if output_path else ()):
        identifier = str(row.get("custom_id") or "")
        if identifier not in by_id or identifier in seen:
            failures.append({"sample_id": identifier, "error": "unknown_or_duplicate_identity"})
            continue
        seen.add(identifier)
        response = row.get("response") or {}
        if int(response.get("status_code") or 0) != 200:
            failures.append({"sample_id": identifier, "error": "api_response_error"})
            continue
        body = response.get("body") or {}
        usage = body.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            content = body["choices"][0]["message"]["content"]
            value = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            failures.append({"sample_id": identifier, "error": f"invalid_json:{type(exc).__name__}"})
            continue
        errors = validate_response(value, by_id[identifier])
        if errors:
            failures.append({"sample_id": identifier, "error": ";".join(errors)})
            continue
        write_json_atomic(
            prediction_dir / f"{identifier}.json",
            to_prediction(by_id[identifier], value, profile.name),
        )
        completed += 1
    for row in (_read_jsonl(error_path) if error_path else ()):
        identifier = str(row.get("custom_id") or "")
        seen.add(identifier)
        response = row.get("response") or {}
        body = response.get("body") or {}
        error = body.get("error") or row.get("error") or {}
        failures.append(
            {
                "sample_id": identifier,
                "error": str(error.get("message") or error.get("code") or "batch_request_failed")[:1_000],
            }
        )
    for identifier in sorted(set(by_id) - seen):
        failures.append({"sample_id": identifier, "error": "missing_output"})
    _write_jsonl(config.model_root(profile.name) / "failures.jsonl", failures)
    elapsed = max(
        0,
        int(state.get("completed_at") or 0) - int(state.get("created_at") or 0),
    )
    actual_cost = _cost(profile, prompt_tokens, completion_tokens)
    manifest = {
        "benchmark_version": BENCHMARK_VERSION,
        "prompt_version": PROMPT_VERSION,
        "profile": profile.name,
        "model": profile.model,
        "sample_rows": len(selected),
        "completed_rows": completed,
        "failure_rows": len(failures),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "actual_batch_cost_usd": str(actual_cost),
        "batch_elapsed_seconds": elapsed,
        "articles_per_minute": round(len(seen) / (elapsed / 60), 4) if elapsed else 0.0,
        "created_at": state.get("created_at"),
        "completed_at": state.get("completed_at"),
    }
    write_json_atomic(config.model_root(profile.name) / "manifest.json", manifest)


def write_comparison(
    config: BenchmarkConfig,
    selected: tuple[CollectionItem, ...],
) -> Path:
    models: dict[str, dict[str, Any]] = {}
    for profile in config.profiles:
        root = config.model_root(profile)
        manifest = read_json(root / "manifest.json")
        prediction_dir = root / "predictions"
        metrics = evaluate_predictions(
            selected,
            prediction_dir=prediction_dir,
            canonical_concepts=True,
            missing_as_failure=True,
        )
        quality = quality_score(metrics) if metrics else 0.0
        models[profile] = {"manifest": manifest, "metrics": metrics, "quality_score": quality}
        write_json_atomic(root / "metrics.json", metrics)
    ranking = sorted(
        models,
        key=lambda name: (
            -models[name]["quality_score"],
            Decimal(models[name]["manifest"]["actual_batch_cost_usd"]),
        ),
    )
    payload = {
        "benchmark_version": BENCHMARK_VERSION,
        "selection": read_json(config.runtime_root / "selection.json"),
        "models": models,
        "ranking": ranking,
    }
    write_json_atomic(config.runtime_root / "comparison.json", payload)
    report = config.runtime_root / "COMPARISON.md"
    report.write_text(_comparison_markdown(payload), encoding="utf-8")
    return report


def quality_score(metrics: Mapping[str, Any]) -> float:
    values = [
        float(metrics["extraction_decision"]["macro_f1"]),
        float(metrics["ticker_scope"]["f1"]),
        float(metrics["content_role"]["macro_f1"]),
        float(metrics["source_origin"]["macro_f1"]),
        float(metrics["semantic_direction"]["macro_f1"]),
        float(metrics["event_concepts"]["f1"]),
        *(
            float(metrics["eligibility"][field]["f1"])
            for field in (
                "forecast_trigger_eligible",
                "reaction_evaluation_eligible",
                "issuer_history_context_eligible",
            )
        ),
    ]
    return round(sum(values) / len(values), 6)


def print_plan(plan: Mapping[str, Any]) -> None:
    for row in plan["models"]:
        print(
            f"PLAN {row['name']} rows={row['request_rows']} "
            f"input_tokens<={int(row['estimated_input_tokens']):,} "
            f"output_tokens<={int(row['reserved_output_tokens']):,} "
            f"protected=${Decimal(row['protected_cost_usd']):.4f}",
            flush=True,
        )
    print(
        f"PROTECTED TOTAL ${Decimal(str(plan['protected_cost_usd'])):.4f} / "
        f"${Decimal(str(plan['hard_max_cost_usd'])):.2f}",
        flush=True,
    )


def print_status(config: BenchmarkConfig) -> None:
    for profile in config.profiles:
        path = config.model_root(profile) / "state.json"
        state = read_json(path) if path.exists() else {}
        counts = state.get("request_counts") or {}
        print(
            f"STATUS {profile} {state.get('status') or 'not_submitted'} "
            f"completed={counts.get('completed', 0)} failed={counts.get('failed', 0)} "
            f"total={counts.get('total', 0)}",
            flush=True,
        )


def _comparison_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# OpenAI model benchmark against 100 human-reviewed News articles",
        "",
        "The sample was selected deterministically from semantic gold strata without consulting V5 errors. "
        "The quality score is the unweighted mean of extraction F1, ticker F1, role macro F1, origin macro F1, "
        "direction macro F1, concept-family F1, and the three eligibility F1 scores.",
        "",
        "All 100 selected articles are scored. A malformed, truncated, or contract-invalid response is retained "
        "as a missing prediction rather than removed from the denominator.",
        "",
        "Batch elapsed time includes OpenAI queueing and is throughput evidence, not live-request latency.",
        "",
        "| Rank | Model | Valid | Quality | Ticker P/R/F1 | Direction F1 | Forecast F1 | Cost | Batch time | Articles/min |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, name in enumerate(payload["ranking"], 1):
        row = payload["models"][name]
        metrics = row["metrics"]
        manifest = row["manifest"]
        ticker = metrics.get("ticker_scope") or {}
        direction = metrics.get("semantic_direction") or {}
        forecast = (metrics.get("eligibility") or {}).get("forecast_trigger_eligible") or {}
        lines.append(
            f"| {rank} | {name} | {manifest['completed_rows']}/{manifest['sample_rows']} | "
            f"{row['quality_score']:.3f} | {ticker.get('precision', 0):.3f}/"
            f"{ticker.get('recall', 0):.3f}/{ticker.get('f1', 0):.3f} | "
            f"{direction.get('macro_f1', 0):.3f} | {forecast.get('f1', 0):.3f} | "
            f"${Decimal(manifest['actual_batch_cost_usd']):.4f} | "
            f"{_duration(int(manifest['batch_elapsed_seconds']))} | "
            f"{float(manifest['articles_per_minute']):.2f} |"
        )
    lines.extend(
        (
            "",
            "## Field-level comparison",
            "",
            "| Model | Decision F1 | Role F1 | Origin F1 | Direction F1 | Concept F1 | Forecast F1 | Reaction F1 | History F1 | Observed $/1K |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        )
    )
    for name in payload["ranking"]:
        row = payload["models"][name]
        metrics = row["metrics"]
        eligibility = metrics["eligibility"]
        cost_per_thousand = Decimal(row["manifest"]["actual_batch_cost_usd"]) * 10
        lines.append(
            f"| {name} | {metrics['extraction_decision']['macro_f1']:.3f} | "
            f"{metrics['content_role']['macro_f1']:.3f} | "
            f"{metrics['source_origin']['macro_f1']:.3f} | "
            f"{metrics['semantic_direction']['macro_f1']:.3f} | "
            f"{metrics['event_concepts']['f1']:.3f} | "
            f"{eligibility['forecast_trigger_eligible']['f1']:.3f} | "
            f"{eligibility['reaction_evaluation_eligible']['f1']:.3f} | "
            f"{eligibility['issuer_history_context_eligible']['f1']:.3f} | "
            f"${cost_per_thousand:.2f} |"
        )
    lines.extend(("", "## Sample distribution", "", "```json", json.dumps(payload["selection"]["distribution"], indent=2), "```", ""))
    return "\n".join(lines)


def _selection_signature(item: CollectionItem) -> tuple[Any, ...]:
    units = item.truth.get("issuer_units") or ()
    direction = tuple(sorted({str(unit["semantic_direction"]) for unit in units})) or ("none",)
    eligible = (
        any(bool(unit.get("forecast_trigger_eligible")) for unit in units),
        any(bool(unit.get("reaction_evaluation_eligible")) for unit in units),
        any(bool(unit.get("issuer_history_context_eligible")) for unit in units),
    )
    issuer_bucket = "0" if not units else "1" if len(units) == 1 else "2+"
    return (
        item.truth["extraction_decision"],
        item.truth["content_role"],
        item.truth["source_origin"],
        issuer_bucket,
        direction,
        eligible,
    )


def _selection_hash(items: Iterable[CollectionItem]) -> str:
    return _json_hash([
        {
            "sample_id": item.sample_id,
            "source_text_sha256": item.truth["source_text_sha256"],
            "annotation_sha256": item.truth["annotation_sha256"],
        }
        for item in items
    ])


def _stable_order(value: str) -> str:
    return hashlib.sha256(f"{SELECTION_VERSION}|{value}".encode()).hexdigest()


def output_token_budget(
    item: CollectionItem,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Reserve enough strict-JSON capacity for broad multi-issuer articles."""
    publication = item.blinded.get("publication") or {}
    candidates = item.blinded.get("point_in_time_issuer_candidates") or ()
    provider_tickers = publication.get("provider_tickers") or ()
    issuer_capacity = max(len(candidates), len(provider_tickers))
    required = 768 + issuer_capacity * 128
    return min(maximum, max(minimum, required))


def _conservative_tokens(value: str) -> int:
    return (len(value.encode("utf-8")) + 2) // 3


def _cost(profile: ModelProfile, input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * profile.batch_input_usd_per_million
        + Decimal(output_tokens) * profile.batch_output_usd_per_million
    ) / Decimal(1_000_000)


def _model_plan(plan: Mapping[str, Any], profile: str) -> Mapping[str, Any]:
    return next(row for row in plan["models"] if row["name"] == profile)


def _state_from_batch(
    batch: Mapping[str, Any],
    plan: Mapping[str, Any],
    model_plan: Mapping[str, Any],
) -> dict[str, Any]:
    counts = batch.get("request_counts") or {}
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "profile": model_plan["name"],
        "model": model_plan["model"],
        "selection_sha256": plan["selection_sha256"],
        "input_sha256": model_plan["input_sha256"],
        "input_file_id": batch.get("input_file_id"),
        "batch_id": batch.get("id"),
        "status": batch.get("status"),
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
        "created_at": batch.get("created_at"),
        "completed_at": batch.get("completed_at"),
        "request_counts": {
            "total": int(counts.get("total") or 0),
            "completed": int(counts.get("completed") or 0),
            "failed": int(counts.get("failed") or 0),
        },
    }


def _validate_state(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    model_plan: Mapping[str, Any],
) -> None:
    if not state:
        return
    expected = {
        "benchmark_version": BENCHMARK_VERSION,
        "profile": model_plan["name"],
        "model": model_plan["model"],
        "selection_sha256": plan["selection_sha256"],
        "input_sha256": model_plan["input_sha256"],
    }
    drift = [key for key, value in expected.items() if state.get(key) != value]
    if drift:
        raise RuntimeError(f"Existing state contract drift: {', '.join(drift)}")


def _find_remote_batch(
    client: OpenAIClient,
    profile: str,
    selection_sha256: str,
) -> Mapping[str, Any] | None:
    matches = []
    for batch in client.list_batches(limit=100):
        metadata = batch.get("metadata") or {}
        if (
            metadata.get("experiment") == BENCHMARK_VERSION
            and metadata.get("profile") == profile
            and metadata.get("selection") == selection_sha256[:32]
        ):
            matches.append(batch)
    if len(matches) > 1:
        raise RuntimeError(f"Multiple remote batches match {profile}; refusing ambiguity.")
    return matches[0] if matches else None


def _write_or_validate_plan(path: Path, plan: Mapping[str, Any]) -> None:
    if path.exists():
        existing = read_json(path)
        if _json_hash(existing) != _json_hash(plan):
            raise RuntimeError("Frozen benchmark plan drift; use a new runtime root.")
        return
    write_json_atomic(path, dict(plan))


def _write_or_validate_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise RuntimeError(f"Frozen request file drift: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duration(seconds: int) -> str:
    minutes, second = divmod(max(0, seconds), 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:d}:{minute:02d}:{second:02d}"
