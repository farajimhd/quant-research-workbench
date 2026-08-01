from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.news_labeling.openai_batch_v1.openai_api import OpenAIClient

from .comparison import CollectionItem
from .openai_gold_benchmark import (
    MODEL_PROFILES,
    PROMPT_VERSION,
    ModelProfile,
    batch_request,
    output_token_budget,
    to_prediction,
    validate_response,
)
from .sol_teacher_corpus import CORPUS_VERSION, load_teacher_items
from .storage import assert_runtime_root, read_json, write_json_atomic


TEACHER_LABEL_VERSION = "news_sol_teacher_labels_v1"
HARD_MAX_COST_USD = Decimal("250.00")
DEFAULT_EXPECTED_OUTPUT_TOKENS = 350
DEFAULT_CHUNK_ROWS = 250
DEFAULT_MAX_ENQUEUED_INPUT_TOKENS = 1_200_000
DEFAULT_MAX_BATCH_ATTEMPTS = 10
PLAN_VERSION = 2
MAX_BATCH_FILE_BYTES = 180 * 1024 * 1024
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
RETRYABLE_BATCH_ERROR_CODES = {
    "token_limit_exceeded",
    "rate_limit_exceeded",
    "server_error",
}

# GPT-5.6 Sol Batch rates. The maximum input rate is the cache-write rate and
# is used for authorization reserves; actual accounting separates cache reads,
# cache writes, and uncached input when the API reports each component.
BATCH_UNCACHED_INPUT_USD_PER_MILLION = Decimal("2.50")
BATCH_CACHED_INPUT_USD_PER_MILLION = Decimal("0.25")
BATCH_CACHE_WRITE_USD_PER_MILLION = Decimal("3.125")
BATCH_OUTPUT_USD_PER_MILLION = Decimal("15.00")


@dataclass(frozen=True, slots=True)
class TeacherBatchConfig:
    corpus_root: Path
    runtime_root: Path
    chunk_rows: int = DEFAULT_CHUNK_ROWS
    max_output_tokens: int = 2_048
    max_dynamic_output_tokens: int = 16_384
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS
    poll_seconds: int = 30
    max_enqueued_input_tokens: int = DEFAULT_MAX_ENQUEUED_INPUT_TOKENS
    max_batch_attempts: int = DEFAULT_MAX_BATCH_ATTEMPTS
    hard_max_cost_usd: Decimal = HARD_MAX_COST_USD
    base_url: str = "https://api.openai.com/v1"
    project_id: str = ""


def run_teacher_batch(
    config: TeacherBatchConfig,
    *,
    execute: bool,
    authorized_cost_usd: Decimal,
    no_wait: bool,
) -> int:
    assert_runtime_root(config.corpus_root)
    assert_runtime_root(config.runtime_root)
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    if config.chunk_rows < 1 or config.chunk_rows > 5_000:
        raise ValueError("chunk_rows must be between 1 and 5000")
    items = _collection_items(config.corpus_root)
    plan = ensure_teacher_plan(config, items)
    print_teacher_plan(plan)
    if not execute:
        print("PLANNED | no OpenAI request was made", flush=True)
        return 0

    expected = Decimal(str(plan["expected_cost_usd"]))
    if authorized_cost_usd < expected:
        raise RuntimeError(
            f"Authorization ${authorized_cost_usd:.2f} is below the exact-input "
            f"expected plan ${expected:.6f}."
        )
    if authorized_cost_usd > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Authorization ${authorized_cost_usd:.2f} exceeds the hard maximum "
            f"${config.hard_max_cost_usd:.2f}."
        )
    client = OpenAIClient(
        os.environ.get("OPENAI_API_KEY", ""),
        project_id=config.project_id,
        base_url=config.base_url,
        timeout_seconds=180,
    )
    profile = MODEL_PROFILES["gpt-5.6-sol"]
    if profile.model not in client.model_ids():
        raise RuntimeError(
            f"Authenticated project does not expose requested model {profile.model}."
        )

    while True:
        reconcile_all(client, config, items, plan)
        summary = budget_summary(config, plan)
        if summary["completed_chunks"] == len(plan["chunks"]):
            report_path = write_teacher_report(config, plan)
            failures = int(summary["failure_rows"])
            print(
                f"COMPLETED | labels={summary['completed_rows']:,}/{len(items):,} "
                f"failures={failures:,} billed_estimate=${summary['actual_cost_usd']:.6f} "
                f"report={report_path}",
                flush=True,
            )
            return 1 if failures else 0

        submitted = submit_affordable_chunks(
            client,
            config,
            plan,
            authorized_cost_usd=authorized_cost_usd,
        )
        summary = budget_summary(config, plan)
        print_teacher_status(summary, len(plan["chunks"]), len(items))
        if no_wait:
            print(
                f"SUBMITTED | new_chunks={submitted}; rerun without --no-wait to collect",
                flush=True,
            )
            return 0
        if (
            not submitted
            and int(summary["active_chunks"]) == 0
            and int(summary["retry_pending_chunks"]) == 0
        ):
            raise RuntimeError(
                "No remaining chunk fits under the authorized rolling maximum. "
                f"actual=${summary['actual_cost_usd']:.6f}, "
                f"authorized=${authorized_cost_usd:.2f}. Increase authorization only "
                f"within the ${config.hard_max_cost_usd:.2f} hard limit."
            )
        time.sleep(max(5, config.poll_seconds))


def ensure_teacher_plan(
    config: TeacherBatchConfig,
    items: tuple[CollectionItem, ...],
) -> dict[str, Any]:
    """Create the token-bounded v2 plan once and validate it on every resume.

    The v1 run may already contain durable labels and billed work.  V2 freezes
    those identities as prior work and plans only missing articles, avoiding
    both duplicate requests and mutation of the original forensic artifacts.
    """
    path = config.runtime_root / "plan_v2.json"
    selection_sha256 = _selection_hash(items)
    if path.exists():
        plan = read_json(path)
        expected = {
            "plan_version": PLAN_VERSION,
            "teacher_label_version": TEACHER_LABEL_VERSION,
            "teacher_corpus_version": CORPUS_VERSION,
            "selection_sha256": selection_sha256,
            "sample_size": len(items),
            "max_enqueued_input_tokens": config.max_enqueued_input_tokens,
        }
        drift = [key for key, value in expected.items() if plan.get(key) != value]
        if drift:
            raise RuntimeError(f"existing teacher plan v2 drift: {', '.join(drift)}")
        _validate_prior_labels(config, plan)
        return plan

    by_id = {item.sample_id: item for item in items}
    labels_root = config.runtime_root / "labels"
    prior_ids = tuple(
        sorted(path.stem for path in labels_root.glob("*.json") if path.stem in by_id)
    )
    prior_hash = _label_set_hash(labels_root, prior_ids)
    prior_cost = _legacy_actual_cost(config.runtime_root / "chunks")
    pending = tuple(item for item in items if item.sample_id not in set(prior_ids))
    plan = build_teacher_plan(
        config,
        pending,
        total_items=items,
        prior_completed_sample_ids=prior_ids,
        prior_labels_sha256=prior_hash,
        prior_actual_cost_usd=prior_cost,
    )
    write_json_atomic(path, plan)
    print(
        f"PLAN V2 | reused_labels={len(prior_ids):,} "
        f"pending={len(pending):,} prior_cost=${prior_cost:.6f} "
        f"token_limit={config.max_enqueued_input_tokens:,}",
        flush=True,
    )
    return plan


def build_teacher_plan(
    config: TeacherBatchConfig,
    items: tuple[CollectionItem, ...],
    *,
    total_items: tuple[CollectionItem, ...] | None = None,
    prior_completed_sample_ids: tuple[str, ...] = (),
    prior_labels_sha256: str = "",
    prior_actual_cost_usd: Decimal = Decimal("0"),
) -> dict[str, Any]:
    profile = MODEL_PROFILES["gpt-5.6-sol"]
    chunks_out: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    current_items: list[CollectionItem] = []
    current_bytes = 0
    current_tokens = 0

    def flush() -> None:
        nonlocal current_rows, current_items, current_bytes, current_tokens
        if not current_rows:
            return
        chunk_index = len(chunks_out) + 1
        path = config.runtime_root / "inputs_v2" / f"chunk_{chunk_index:04d}.jsonl"
        _write_or_validate_jsonl(path, current_rows)
        input_tokens = sum(
            _conservative_tokens(json.dumps(row["body"], ensure_ascii=False))
            for row in current_rows
        )
        max_outputs = sum(
            int(row["body"]["max_completion_tokens"]) for row in current_rows
        )
        expected_outputs = sum(
            expected_output_budget(item, config) for item in current_items
        )
        chunks_out.append(
            {
                "chunk_index": chunk_index,
                "request_rows": len(current_rows),
                "sample_ids": [item.sample_id for item in current_items],
                "input_path": str(path),
                "input_sha256": _file_hash(path),
                "input_bytes": path.stat().st_size,
                "estimated_input_tokens": input_tokens,
                "expected_output_tokens": expected_outputs,
                "maximum_output_tokens": max_outputs,
                "expected_cost_usd": str(
                    expected_cost(input_tokens, expected_outputs)
                ),
                "maximum_reserved_cost_usd": str(
                    maximum_reserved_cost(input_tokens, max_outputs)
                ),
            }
        )
        current_rows = []
        current_items = []
        current_bytes = 0
        current_tokens = 0

    for item in items:
        request = batch_request(
            item,
            profile,
            output_token_budget(
                item,
                minimum=config.max_output_tokens,
                maximum=config.max_dynamic_output_tokens,
            ),
        )
        encoded = (
            json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        request_tokens = _conservative_tokens(
            json.dumps(request["body"], ensure_ascii=False)
        )
        if request_tokens > config.max_enqueued_input_tokens:
            raise RuntimeError(
                "single request exceeds the configured enqueued-token ceiling: "
                f"{item.sample_id} tokens={request_tokens:,} "
                f"limit={config.max_enqueued_input_tokens:,}"
            )
        if len(encoded) > MAX_BATCH_FILE_BYTES:
            raise RuntimeError(f"single request exceeds Batch file limit: {item.sample_id}")
        if current_rows and (
            len(current_rows) >= config.chunk_rows
            or current_bytes + len(encoded) > MAX_BATCH_FILE_BYTES
            or current_tokens + request_tokens > config.max_enqueued_input_tokens
        ):
            flush()
        current_rows.append(request)
        current_items.append(item)
        current_bytes += len(encoded)
        current_tokens += request_tokens
    flush()
    all_items = total_items or items
    selection_sha256 = _selection_hash(all_items)
    return {
        "plan_version": PLAN_VERSION,
        "teacher_label_version": TEACHER_LABEL_VERSION,
        "teacher_corpus_version": CORPUS_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": profile.model,
        "selection_sha256": selection_sha256,
        "sample_size": len(all_items),
        "pending_sample_size": len(items),
        "prior_completed_rows": len(prior_completed_sample_ids),
        "prior_completed_sample_ids": list(prior_completed_sample_ids),
        "prior_labels_sha256": prior_labels_sha256,
        "prior_actual_cost_usd": str(prior_actual_cost_usd),
        "chunk_rows": config.chunk_rows,
        "max_enqueued_input_tokens": config.max_enqueued_input_tokens,
        "chunk_count": len(chunks_out),
        "expected_cost_usd": str(
            sum(Decimal(row["expected_cost_usd"]) for row in chunks_out)
        ),
        "maximum_cost_usd": str(
            sum(Decimal(row["maximum_reserved_cost_usd"]) for row in chunks_out)
        ),
        "hard_max_cost_usd": str(config.hard_max_cost_usd),
        "pricing": {
            "batch_uncached_input_usd_per_million": str(
                BATCH_UNCACHED_INPUT_USD_PER_MILLION
            ),
            "batch_cached_input_usd_per_million": str(
                BATCH_CACHED_INPUT_USD_PER_MILLION
            ),
            "batch_cache_write_usd_per_million": str(
                BATCH_CACHE_WRITE_USD_PER_MILLION
            ),
            "batch_output_usd_per_million": str(BATCH_OUTPUT_USD_PER_MILLION),
            "authorization_policy": (
                "rolling actual spend plus maximum active and next-chunk reserve"
            ),
        },
        "chunks": chunks_out,
    }


def _validate_prior_labels(
    config: TeacherBatchConfig, plan: Mapping[str, Any]
) -> None:
    sample_ids = tuple(str(value) for value in plan["prior_completed_sample_ids"])
    actual = _label_set_hash(config.runtime_root / "labels", sample_ids)
    if actual != str(plan.get("prior_labels_sha256") or ""):
        raise RuntimeError(
            "durable labels reused by plan v2 are missing or have changed; "
            "restore the original label files before resuming"
        )


def _label_set_hash(root: Path, sample_ids: tuple[str, ...]) -> str:
    rows: list[dict[str, str]] = []
    for sample_id in sample_ids:
        path = root / f"{sample_id}.json"
        if not path.exists():
            return "missing"
        rows.append({"sample_id": sample_id, "sha256": _file_hash(path)})
    return _json_hash(rows)


def _legacy_actual_cost(root: Path) -> Decimal:
    actual = Decimal("0")
    if not root.exists():
        return actual
    for path in root.glob("chunk_*/manifest.json"):
        manifest = read_json(path)
        if int(manifest.get("completed_rows") or 0) > 0:
            actual += Decimal(str(manifest.get("actual_batch_cost_usd") or "0"))
    return actual


def reconcile_all(
    client: OpenAIClient,
    config: TeacherBatchConfig,
    items: tuple[CollectionItem, ...],
    plan: Mapping[str, Any],
) -> None:
    by_id = {item.sample_id: item for item in items}
    for chunk in plan["chunks"]:
        state_path = _state_path(config, chunk)
        if not state_path.exists():
            continue
        state = read_json(state_path)
        _validate_chunk_state(state, plan, chunk)
        if state.get("collected"):
            continue
        if not state.get("batch_id"):
            continue
        batch = client.retrieve_batch(str(state["batch_id"]))
        state.update(_state_from_batch(batch, plan, chunk))
        write_json_atomic(state_path, state)
        if str(state.get("status") or "") not in TERMINAL_BATCH_STATUSES:
            continue
        if _is_retryable_zero_work_failure(state):
            _schedule_retry(config, state_path, state)
            continue
        output_path = _chunk_root(config, chunk) / "output.jsonl"
        if state.get("output_file_id") and not output_path.exists():
            client.download_file(str(state["output_file_id"]), output_path)
        error_path = _chunk_root(config, chunk) / "error.jsonl"
        if state.get("error_file_id") and not error_path.exists():
            client.download_file(str(state["error_file_id"]), error_path)
        collect_chunk(
            config,
            chunk,
            state,
            by_id=by_id,
            output_path=output_path if output_path.exists() else None,
            error_path=error_path if error_path.exists() else None,
        )
        state["collected"] = True
        write_json_atomic(state_path, state)


def submit_affordable_chunks(
    client: OpenAIClient,
    config: TeacherBatchConfig,
    plan: Mapping[str, Any],
    *,
    authorized_cost_usd: Decimal,
) -> int:
    summary = budget_summary(config, plan)
    remote_batches = _remote_batch_index(client, plan)
    committed = Decimal(str(summary["actual_cost_usd"])) + Decimal(
        str(summary["active_maximum_reserve_usd"])
    )
    active_tokens = int(summary["active_enqueued_input_tokens"])
    now = int(time.time())
    submitted = 0
    for chunk in plan["chunks"]:
        state_path = _state_path(config, chunk)
        state = read_json(state_path) if state_path.exists() else {}
        if state:
            _validate_chunk_state(state, plan, chunk)
            if state.get("collected") or state.get("batch_id"):
                continue
            if int(state.get("next_retry_at") or 0) > now:
                continue
            if int(state.get("attempt_count") or 0) >= config.max_batch_attempts:
                _settle_exhausted_chunk(config, chunk, state)
                continue
        reserve = Decimal(str(chunk["maximum_reserved_cost_usd"]))
        if committed + reserve > authorized_cost_usd:
            continue
        chunk_tokens = int(chunk["estimated_input_tokens"])
        if active_tokens + chunk_tokens > config.max_enqueued_input_tokens:
            continue
        submit_chunk(
            client,
            config,
            plan,
            chunk,
            recovered=(
                None
                if state.get("attempt_count")
                else remote_batches.get(int(chunk["chunk_index"]))
            ),
            remote_lookup_complete=True,
        )
        committed += reserve
        active_tokens += chunk_tokens
        submitted += 1
    return submitted


def submit_chunk(
    client: OpenAIClient,
    config: TeacherBatchConfig,
    plan: Mapping[str, Any],
    chunk: Mapping[str, Any],
    *,
    recovered: Mapping[str, Any] | None = None,
    remote_lookup_complete: bool = False,
) -> None:
    root = _chunk_root(config, chunk)
    root.mkdir(parents=True, exist_ok=True)
    state_path = _state_path(config, chunk)
    state = read_json(state_path) if state_path.exists() else {}
    if state:
        _validate_chunk_state(state, plan, chunk)
    if state.get("batch_id"):
        return
    if not remote_lookup_complete:
        recovered = _find_remote_batch(client, plan, chunk)
    if recovered:
        write_json_atomic(state_path, _state_from_batch(recovered, plan, chunk))
        return
    if not state.get("input_file_id"):
        uploaded = client.upload_batch_file(Path(str(chunk["input_path"])))
        state = {
            "teacher_label_version": TEACHER_LABEL_VERSION,
            "selection_sha256": plan["selection_sha256"],
            "chunk_index": chunk["chunk_index"],
            "input_sha256": chunk["input_sha256"],
            "input_file_id": str(uploaded["id"]),
            "status": "uploaded",
        }
        write_json_atomic(state_path, state)
    batch = client.create_batch(
        str(state["input_file_id"]),
        {
            "experiment": TEACHER_LABEL_VERSION,
            "selection": str(plan["selection_sha256"])[:32],
            "chunk": f"{int(chunk['chunk_index']):04d}",
            "input": str(chunk["input_sha256"])[:32],
            "attempt": str(int(state.get("attempt_count") or 0) + 1),
        },
    )
    state["attempt_count"] = int(state.get("attempt_count") or 0) + 1
    state.pop("retry_pending", None)
    state.pop("next_retry_at", None)
    state.update(_state_from_batch(batch, plan, chunk))
    write_json_atomic(state_path, state)


def collect_chunk(
    config: TeacherBatchConfig,
    chunk: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    by_id: Mapping[str, CollectionItem],
    output_path: Path | None,
    error_path: Path | None,
) -> None:
    expected_ids = set(str(value) for value in chunk["sample_ids"])
    seen: set[str] = set()
    failures: list[dict[str, Any]] = []
    usage = CounterUsage()
    completed = 0
    for row in (_read_jsonl(output_path) if output_path else ()):
        sample_id = str(row.get("custom_id") or "")
        if sample_id not in expected_ids or sample_id in seen:
            failures.append({"sample_id": sample_id, "error": "unknown_or_duplicate_identity"})
            continue
        seen.add(sample_id)
        response = row.get("response") or {}
        if int(response.get("status_code") or 0) != 200:
            failures.append({"sample_id": sample_id, "error": "api_response_error"})
            continue
        body = response.get("body") or {}
        usage.add(body.get("usage") or {})
        try:
            value = json.loads(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            failures.append(
                {"sample_id": sample_id, "error": f"invalid_json:{type(exc).__name__}"}
            )
            continue
        item = by_id[sample_id]
        errors = validate_response(value, item)
        if errors:
            failures.append({"sample_id": sample_id, "error": ";".join(errors)})
            continue
        prediction = to_prediction(item, value, "gpt-5.6-sol")
        prediction.pop("benchmark_version", None)
        prediction.update(
            {
                "teacher_label_version": TEACHER_LABEL_VERSION,
                "teacher_corpus_version": CORPUS_VERSION,
                "model": "gpt-5.6-sol",
            }
        )
        write_json_atomic(
            config.runtime_root / "labels" / f"{sample_id}.json", prediction
        )
        completed += 1
    for row in (_read_jsonl(error_path) if error_path else ()):
        sample_id = str(row.get("custom_id") or "")
        seen.add(sample_id)
        error = row.get("error") or {}
        failures.append(
            {
                "sample_id": sample_id,
                "error": str(
                    error.get("message") or error.get("code") or "batch_request_failed"
                )[:1_000],
            }
        )
    for sample_id in sorted(expected_ids - seen):
        failures.append({"sample_id": sample_id, "error": "missing_output"})
    _write_jsonl(_chunk_root(config, chunk) / "failures.jsonl", failures)
    elapsed = max(
        0, int(state.get("completed_at") or 0) - int(state.get("created_at") or 0)
    )
    manifest = {
        "teacher_label_version": TEACHER_LABEL_VERSION,
        "chunk_index": chunk["chunk_index"],
        "sample_rows": len(expected_ids),
        "completed_rows": completed,
        "failure_rows": len(failures),
        **usage.as_dict(),
        "actual_batch_cost_usd": str(usage.cost()),
        "cost_accounting_basis": (
            "reported_cache_components"
            if usage.cache_write_reporting_complete
            else "maximum_non_cached_input_rate_due_to_missing_cache_write_breakdown"
        ),
        "batch_elapsed_seconds": elapsed,
    }
    write_json_atomic(_chunk_root(config, chunk) / "manifest.json", manifest)


@dataclass(slots=True)
class CounterUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cache_write_reporting_complete: bool = True

    def add(self, usage: Mapping[str, Any]) -> None:
        input_tokens = int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        cache_write_keys = (
            "cache_write_tokens",
            "cache_creation_tokens",
            "cache_creation_input_tokens",
        )
        reported_key = next((key for key in cache_write_keys if key in details), None)
        if reported_key is None:
            self.cache_write_reporting_complete = False
        cache_write = int(details.get(reported_key) or 0) if reported_key else 0
        self.input_tokens += input_tokens
        self.cached_input_tokens += min(cached, input_tokens)
        self.cache_write_tokens += min(cache_write, max(0, input_tokens - cached))
        self.output_tokens += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )

    @property
    def uncached_input_tokens(self) -> int:
        return max(
            0,
            self.input_tokens - self.cached_input_tokens - self.cache_write_tokens,
        )

    def cost(self) -> Decimal:
        noncached_rate = (
            BATCH_UNCACHED_INPUT_USD_PER_MILLION
            if self.cache_write_reporting_complete
            else BATCH_CACHE_WRITE_USD_PER_MILLION
        )
        return (
            Decimal(self.uncached_input_tokens)
            * noncached_rate
            + Decimal(self.cached_input_tokens)
            * BATCH_CACHED_INPUT_USD_PER_MILLION
            + Decimal(self.cache_write_tokens)
            * BATCH_CACHE_WRITE_USD_PER_MILLION
            + Decimal(self.output_tokens) * BATCH_OUTPUT_USD_PER_MILLION
        ) / Decimal(1_000_000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_reporting_complete": self.cache_write_reporting_complete,
        }


def budget_summary(
    config: TeacherBatchConfig, plan: Mapping[str, Any]
) -> dict[str, Any]:
    actual = Decimal(str(plan.get("prior_actual_cost_usd") or "0"))
    active_reserve = Decimal("0")
    completed_chunks = 0
    active_chunks = 0
    completed_rows = int(plan.get("prior_completed_rows") or 0)
    failure_rows = 0
    active_tokens = 0
    retry_pending_chunks = 0
    pending_chunks = 0
    for chunk in plan["chunks"]:
        state_path = _state_path(config, chunk)
        manifest_path = _chunk_root(config, chunk) / "manifest.json"
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            actual += Decimal(str(manifest["actual_batch_cost_usd"]))
            completed_rows += int(manifest["completed_rows"])
            failure_rows += int(manifest["failure_rows"])
            completed_chunks += 1
        elif state_path.exists():
            state = read_json(state_path)
            if state.get("batch_id"):
                active_chunks += 1
                active_reserve += Decimal(str(chunk["maximum_reserved_cost_usd"]))
                active_tokens += int(chunk["estimated_input_tokens"])
            elif state.get("retry_pending"):
                retry_pending_chunks += 1
            else:
                pending_chunks += 1
        else:
            pending_chunks += 1
    return {
        "actual_cost_usd": actual,
        "active_maximum_reserve_usd": active_reserve,
        "completed_chunks": completed_chunks,
        "active_chunks": active_chunks,
        "active_enqueued_input_tokens": active_tokens,
        "retry_pending_chunks": retry_pending_chunks,
        "pending_chunks": pending_chunks,
        "completed_rows": completed_rows,
        "failure_rows": failure_rows,
    }


def expected_output_budget(item: CollectionItem, config: TeacherBatchConfig) -> int:
    candidates = len(item.blinded.get("point_in_time_issuer_candidates") or ())
    value = config.expected_output_tokens + max(0, candidates - 1) * 110
    return min(
        output_token_budget(
            item,
            minimum=config.max_output_tokens,
            maximum=config.max_dynamic_output_tokens,
        ),
        value,
    )


def expected_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * BATCH_UNCACHED_INPUT_USD_PER_MILLION
        + Decimal(output_tokens) * BATCH_OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)


def maximum_reserved_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * BATCH_CACHE_WRITE_USD_PER_MILLION
        + Decimal(output_tokens) * BATCH_OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)


def print_teacher_plan(plan: Mapping[str, Any]) -> None:
    total_input = sum(int(row["estimated_input_tokens"]) for row in plan["chunks"])
    total_expected_output = sum(int(row["expected_output_tokens"]) for row in plan["chunks"])
    print(
        f"SOL TEACHER PLAN | articles={int(plan['sample_size']):,} "
        f"chunks={int(plan['chunk_count']):,} input_tokens={total_input:,} "
        f"expected_output_tokens={total_expected_output:,}",
        flush=True,
    )
    print(
        f"COST | expected_uncached=${Decimal(str(plan['expected_cost_usd'])):.4f} "
        f"all_tokens_maximum=${Decimal(str(plan['maximum_cost_usd'])):.4f} "
        f"hard_limit=${Decimal(str(plan['hard_max_cost_usd'])):.2f}",
        flush=True,
    )


def print_teacher_status(
    summary: Mapping[str, Any], chunk_count: int, item_count: int
) -> None:
    print(
        f"STATUS | chunks={int(summary['completed_chunks']):,}/{chunk_count:,} "
        f"active={int(summary['active_chunks']):,} "
        f"retry={int(summary['retry_pending_chunks']):,} "
        f"queued_tokens={int(summary['active_enqueued_input_tokens']):,} "
        f"labels={int(summary['completed_rows']):,}/{item_count:,} "
        f"failures={int(summary['failure_rows']):,} "
        f"actual=${Decimal(str(summary['actual_cost_usd'])):.4f} "
        f"active_max_reserve=${Decimal(str(summary['active_maximum_reserve_usd'])):.4f}",
        flush=True,
    )


def write_teacher_report(config: TeacherBatchConfig, plan: Mapping[str, Any]) -> Path:
    summary = budget_summary(config, plan)
    payload = {
        "teacher_label_version": TEACHER_LABEL_VERSION,
        "teacher_corpus_version": CORPUS_VERSION,
        "selection_sha256": plan["selection_sha256"],
        "sample_size": plan["sample_size"],
        **{
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in summary.items()
        },
    }
    write_json_atomic(config.runtime_root / "result.json", payload)
    path = config.runtime_root / "RESULT.md"
    path.write_text(
        "\n".join(
            (
                "# Sol teacher-label result",
                "",
                f"- Articles: {int(plan['sample_size']):,}",
                f"- Completed labels: {int(summary['completed_rows']):,}",
                f"- Failures: {int(summary['failure_rows']):,}",
                f"- Estimated billed cost: ${Decimal(str(summary['actual_cost_usd'])):.6f}",
                f"- Selection hash: `{plan['selection_sha256']}`",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def _collection_items(root: Path) -> tuple[CollectionItem, ...]:
    return tuple(
        CollectionItem(
            sample_id=str(item["sample_id"]),
            split="teacher",
            blinded=item,
            truth={},
        )
        for item in load_teacher_items(root)
    )


def _chunk_root(config: TeacherBatchConfig, chunk: Mapping[str, Any]) -> Path:
    return config.runtime_root / "chunks_v2" / f"chunk_{int(chunk['chunk_index']):04d}"


def _state_path(config: TeacherBatchConfig, chunk: Mapping[str, Any]) -> Path:
    return _chunk_root(config, chunk) / "state.json"


def _state_from_batch(
    batch: Mapping[str, Any],
    plan: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> dict[str, Any]:
    counts = batch.get("request_counts") or {}
    return {
        "teacher_label_version": TEACHER_LABEL_VERSION,
        "selection_sha256": plan["selection_sha256"],
        "chunk_index": chunk["chunk_index"],
        "input_sha256": chunk["input_sha256"],
        "input_file_id": batch.get("input_file_id"),
        "batch_id": batch.get("id"),
        "status": batch.get("status"),
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
        "created_at": batch.get("created_at"),
        "completed_at": batch.get("completed_at"),
        "errors": batch.get("errors"),
        "request_counts": {
            "total": int(counts.get("total") or 0),
            "completed": int(counts.get("completed") or 0),
            "failed": int(counts.get("failed") or 0),
        },
    }


def _batch_error_codes(state: Mapping[str, Any]) -> set[str]:
    errors = state.get("errors") or {}
    rows: Iterable[Any]
    if isinstance(errors, Mapping):
        rows = errors.get("data") or (errors,)
    elif isinstance(errors, list):
        rows = errors
    else:
        rows = ()
    return {
        str(row.get("code") or "").strip()
        for row in rows
        if isinstance(row, Mapping) and row.get("code")
    }


def _is_retryable_zero_work_failure(state: Mapping[str, Any]) -> bool:
    status = str(state.get("status") or "")
    counts = state.get("request_counts") or {}
    if int(counts.get("total") or 0) != 0:
        return False
    if state.get("output_file_id") or state.get("error_file_id"):
        return False
    if status == "expired":
        return True
    return status == "failed" and bool(
        _batch_error_codes(state) & RETRYABLE_BATCH_ERROR_CODES
    )


def _schedule_retry(
    config: TeacherBatchConfig, state_path: Path, state: dict[str, Any]
) -> None:
    attempts = list(state.get("attempts") or ())
    batch_id = str(state.get("batch_id") or "")
    if batch_id and not any(row.get("batch_id") == batch_id for row in attempts):
        attempts.append(
            {
                "batch_id": batch_id,
                "status": state.get("status"),
                "created_at": state.get("created_at"),
                "completed_at": state.get("completed_at"),
                "request_counts": state.get("request_counts"),
                "errors": state.get("errors"),
            }
        )
    attempt_count = max(int(state.get("attempt_count") or 1), len(attempts))
    delay = min(900, 30 * (2 ** min(5, max(0, attempt_count - 1))))
    state.update(
        {
            "attempts": attempts,
            "attempt_count": attempt_count,
            "batch_id": None,
            "status": "retry_wait",
            "retry_pending": True,
            "next_retry_at": int(time.time()) + delay,
        }
    )
    write_json_atomic(state_path, state)
    print(
        f"RETRY | chunk={int(state['chunk_index']):04d} "
        f"attempt={attempt_count}/{config.max_batch_attempts} wait={delay}s "
        f"reason={','.join(sorted(_batch_error_codes(attempts[-1]))) or 'expired'}",
        flush=True,
    )


def _settle_exhausted_chunk(
    config: TeacherBatchConfig,
    chunk: Mapping[str, Any],
    state: dict[str, Any],
) -> None:
    failures = [
        {
            "sample_id": str(sample_id),
            "error": "batch_retry_attempts_exhausted",
        }
        for sample_id in chunk["sample_ids"]
    ]
    _write_jsonl(_chunk_root(config, chunk) / "failures.jsonl", failures)
    write_json_atomic(
        _chunk_root(config, chunk) / "manifest.json",
        {
            "teacher_label_version": TEACHER_LABEL_VERSION,
            "chunk_index": chunk["chunk_index"],
            "sample_rows": len(failures),
            "completed_rows": 0,
            "failure_rows": len(failures),
            "input_tokens": 0,
            "uncached_input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "cache_write_reporting_complete": True,
            "actual_batch_cost_usd": "0",
            "cost_accounting_basis": "no_requests_processed",
            "batch_elapsed_seconds": 0,
        },
    )
    state["collected"] = True
    state["status"] = "retry_exhausted"
    state["retry_pending"] = False
    write_json_atomic(_state_path(config, chunk), state)


def _validate_chunk_state(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> None:
    expected = {
        "teacher_label_version": TEACHER_LABEL_VERSION,
        "selection_sha256": plan["selection_sha256"],
        "chunk_index": chunk["chunk_index"],
        "input_sha256": chunk["input_sha256"],
    }
    drift = [key for key, value in expected.items() if state.get(key) != value]
    if drift:
        raise RuntimeError(f"existing chunk state drift: {', '.join(drift)}")


def _find_remote_batch(
    client: OpenAIClient,
    plan: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    matches: list[Mapping[str, Any]] = []
    for batch in client.list_batches(limit=100):
        metadata = batch.get("metadata") or {}
        if (
            metadata.get("experiment") == TEACHER_LABEL_VERSION
            and metadata.get("selection") == str(plan["selection_sha256"])[:32]
            and metadata.get("chunk") == f"{int(chunk['chunk_index']):04d}"
            and metadata.get("input") == str(chunk["input_sha256"])[:32]
        ):
            if not _is_retryable_zero_work_failure(_state_from_batch(batch, plan, chunk)):
                matches.append(batch)
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple remote batches match chunk {chunk['chunk_index']}"
        )
    return matches[0] if matches else None


def _remote_batch_index(
    client: OpenAIClient, plan: Mapping[str, Any]
) -> dict[int, Mapping[str, Any]]:
    matches: dict[int, Mapping[str, Any]] = {}
    expected_selection = str(plan["selection_sha256"])[:32]
    expected_inputs = {
        int(chunk["chunk_index"]): str(chunk["input_sha256"])[:32]
        for chunk in plan["chunks"]
    }
    for batch in client.list_batches(limit=100):
        metadata = batch.get("metadata") or {}
        if (
            metadata.get("experiment") != TEACHER_LABEL_VERSION
            or metadata.get("selection") != expected_selection
        ):
            continue
        raw_index = str(metadata.get("chunk") or "")
        if not raw_index.isdigit():
            continue
        index = int(raw_index)
        if metadata.get("input") != expected_inputs.get(index):
            continue
        chunk = next(
            row for row in plan["chunks"] if int(row["chunk_index"]) == index
        )
        if _is_retryable_zero_work_failure(_state_from_batch(batch, plan, chunk)):
            continue
        existing = matches.get(index)
        if existing is not None:
            existing_terminal = str(existing.get("status") or "") in TERMINAL_BATCH_STATUSES
            candidate_terminal = str(batch.get("status") or "") in TERMINAL_BATCH_STATUSES
            if not existing_terminal and not candidate_terminal:
                raise RuntimeError(f"multiple active remote batches match chunk {index}")
            if int(batch.get("created_at") or 0) <= int(existing.get("created_at") or 0):
                continue
        matches[index] = batch
    return matches


def _selection_hash(items: Iterable[CollectionItem]) -> str:
    return _json_hash(
        [
            {
                "sample_id": item.sample_id,
                "source_id": item.blinded["source_id"],
                "source_text_sha256": item.blinded["source_text_sha256"],
                "teacher_item_sha256": item.blinded["teacher_item_sha256"],
            }
            for item in items
        ]
    )


def _write_or_validate_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if _json_hash(read_json(path)) != _json_hash(value):
            raise RuntimeError(f"frozen plan drift: {path}")
        return
    write_json_atomic(path, dict(value))


def _write_or_validate_jsonl(
    path: Path, rows: Iterable[Mapping[str, Any]]
) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise RuntimeError(f"frozen request file drift: {path}")
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


def _conservative_tokens(value: str) -> int:
    return (len(value.encode("utf-8")) + 2) // 3


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
