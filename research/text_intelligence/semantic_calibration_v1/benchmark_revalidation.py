from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .candidate_contract import candidate_tickers, repair_item_candidates
from .comparison import CollectionItem, evaluate_predictions, load_collection
from .openai_gold_benchmark import quality_score, to_prediction, validate_response
from .storage import assert_runtime_root, read_json, write_json_atomic


REVALIDATION_VERSION = "news_gold_candidate_revalidation_v1"
SOURCE_OPENAI_VERSION = "news_gold_openai_benchmark_v4"
SOURCE_OSS_VERSION = "news_gold_oss_benchmark_v1"


def run_revalidation(
    *,
    collection_root: Path,
    selection_path: Path,
    openai_runtime: Path,
    oss_runtime: Path,
    output_root: Path,
) -> Path:
    assert_runtime_root(output_root)
    selection = read_json(selection_path)
    sample_ids = tuple(str(value) for value in selection.get("sample_ids") or ())
    if len(sample_ids) != 100 or len(set(sample_ids)) != 100:
        raise RuntimeError("Revalidation requires the exact frozen 100-article population.")
    source_by_id = {item.sample_id: item for item in load_collection(collection_root)}
    source_items = tuple(source_by_id[sample_id] for sample_id in sample_ids)
    items = tuple(repair_item_candidates(item) for item in source_items)
    changed_ids = tuple(
        source.sample_id
        for source, repaired in zip(source_items, items, strict=True)
        if candidate_tickers(source) != candidate_tickers(repaired)
    )
    impossible_before = _truth_outside_contract(source_items)
    impossible_after = _truth_outside_contract(items)
    audit = {
        "revalidation_version": REVALIDATION_VERSION,
        "sample_rows": len(items),
        "candidate_input_changed_ids": list(changed_ids),
        "candidate_input_changed_rows": len(changed_ids),
        "truth_outside_contract_before": impossible_before,
        "truth_outside_contract_after": impossible_after,
        "prompt_v2_changes_all_request_inputs": True,
        "prompt_v2_full_rerun_rows": len(items),
        "generated_at_utc": _utc_now(),
    }
    if impossible_after:
        raise RuntimeError(
            "Corrected candidate contract still makes gold truth impossible: "
            + json.dumps(impossible_after[:10], sort_keys=True)
        )
    output_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_root / "candidate_audit.json", audit)
    rows: list[dict[str, Any]] = []
    if openai_runtime.exists():
        for model_root in sorted((openai_runtime / "models").glob("*")):
            if model_root.is_dir() and (model_root / "output.jsonl").exists():
                rows.append(
                    _revalidate_openai_model(
                        model_root=model_root,
                        items=items,
                        output_root=output_root / "models" / model_root.name,
                    )
                )
    if oss_runtime.exists():
        for model_root in sorted((oss_runtime / "models").glob("*")):
            if model_root.is_dir() and (model_root / "responses").exists():
                rows.append(
                    _revalidate_oss_model(
                        model_root=model_root,
                        items=items,
                        output_root=output_root / "models" / f"gpt-oss-{model_root.name}",
                    )
                )
    rows.sort(key=lambda row: (-row["quality_score"], row["model"]))
    payload = {
        "revalidation_version": REVALIDATION_VERSION,
        "source_openai_version": SOURCE_OPENAI_VERSION,
        "source_oss_version": SOURCE_OSS_VERSION,
        "contract": "candidate_v2_with_original_prompt_outputs",
        "not_exact_prompt_v2": True,
        "audit": audit,
        "models": rows,
        "generated_at_utc": _utc_now(),
    }
    write_json_atomic(output_root / "comparison.json", payload)
    report_path = output_root / "COMPARISON.md"
    report_path.write_text(_report(payload), encoding="utf-8")
    return report_path


def _revalidate_openai_model(
    *, model_root: Path, items: tuple[CollectionItem, ...], output_root: Path
) -> dict[str, Any]:
    values: dict[str, Mapping[str, Any]] = {}
    for row in _read_jsonl(model_root / "output.jsonl"):
        identifier = str(row.get("custom_id") or "")
        try:
            response = row["response"]
            if int(response.get("status_code") or 0) != 200:
                continue
            content = response["body"]["choices"][0]["message"]["content"]
            values[identifier] = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            continue
    source_manifest = read_json(model_root / "manifest.json")
    return _score_values(
        model=model_root.name,
        mode="OpenAI Batch prompt V1 revalidation",
        values=values,
        items=items,
        output_root=output_root,
        source_manifest=source_manifest,
    )


def _revalidate_oss_model(
    *, model_root: Path, items: tuple[CollectionItem, ...], output_root: Path
) -> dict[str, Any]:
    values: dict[str, Mapping[str, Any]] = {}
    for path in (model_root / "responses").glob("*.json"):
        row = read_json(path)
        value = row.get("structured_output")
        if isinstance(value, Mapping):
            values[path.stem] = value
    source_manifest = read_json(model_root / "manifest.json")
    return _score_values(
        model=f"gpt-oss-{model_root.name}",
        mode="local vLLM prompt V1 revalidation",
        values=values,
        items=items,
        output_root=output_root,
        source_manifest=source_manifest,
    )


def _score_values(
    *,
    model: str,
    mode: str,
    values: Mapping[str, Mapping[str, Any]],
    items: tuple[CollectionItem, ...],
    output_root: Path,
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    by_id = {item.sample_id: item for item in items}
    prediction_dir = output_root / "predictions"
    failures: list[dict[str, Any]] = []
    valid = 0
    for item in items:
        value = values.get(item.sample_id)
        if value is None:
            failures.append({"sample_id": item.sample_id, "error": "raw_output_unavailable"})
            continue
        errors = validate_response(value, item)
        if errors:
            failures.append({"sample_id": item.sample_id, "error": ";".join(errors)})
            continue
        prediction = to_prediction(by_id[item.sample_id], value, model)
        prediction["benchmark_version"] = REVALIDATION_VERSION
        prediction["prompt_version"] = str(source_manifest.get("prompt_version") or "prompt_v1")
        write_json_atomic(prediction_dir / f"{item.sample_id}.json", prediction)
        valid += 1
    metrics = evaluate_predictions(
        items,
        prediction_dir=prediction_dir,
        canonical_concepts=True,
        missing_as_failure=True,
    )
    score = quality_score(metrics)
    manifest = {
        "revalidation_version": REVALIDATION_VERSION,
        "model": model,
        "mode": mode,
        "sample_rows": len(items),
        "raw_output_rows": len(values),
        "completed_rows": valid,
        "failure_rows": len(failures),
        "quality_score": score,
        "source_manifest": dict(source_manifest),
        "generated_at_utc": _utc_now(),
    }
    write_json_atomic(output_root / "manifest.json", manifest)
    write_json_atomic(output_root / "metrics.json", metrics)
    write_json_atomic(output_root / "failures.json", failures)
    return {
        "model": model,
        "mode": mode,
        "valid": valid,
        "quality_score": score,
        "direction_f1": metrics["semantic_direction"]["macro_f1"],
        "forecast_f1": metrics["eligibility"]["forecast_trigger_eligible"]["f1"],
        "failure_rows": len(failures),
    }


def _truth_outside_contract(items: Iterable[CollectionItem]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in items:
        allowed = set(candidate_tickers(item))
        missing = sorted(
            str(unit.get("ticker") or "").upper()
            for unit in item.truth.get("issuer_units") or ()
            if str(unit.get("ticker") or "").upper() not in allowed
        )
        if missing:
            failures.append(
                {
                    "sample_id": item.sample_id,
                    "missing_truth_tickers": missing,
                    "allowed_tickers": sorted(allowed),
                }
            )
    return failures


def _report(payload: Mapping[str, Any]) -> str:
    audit = payload["audit"]
    lines = [
        "# Gold benchmark candidate-contract revalidation",
        "",
        "This is a post-hoc revalidation of stored prompt-V1 outputs. It is not an exact prompt-V2 comparison.",
        "",
        f"- Candidate-input changes: {audit['candidate_input_changed_rows']} ({', '.join(audit['candidate_input_changed_ids']) or 'none'})",
        f"- Impossible gold rows before repair: {len(audit['truth_outside_contract_before'])}",
        f"- Impossible gold rows after repair: {len(audit['truth_outside_contract_after'])}",
        f"- Exact prompt-V2 requests requiring rerun: {audit['prompt_v2_full_rerun_rows']}",
        "",
        "| Rank | Model | Source mode | Valid | Quality | Direction F1 | Forecast F1 |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(payload["models"], 1):
        lines.append(
            f"| {rank} | {row['model']} | {row['mode']} | {row['valid']}/100 | "
            f"{row['quality_score']:.3f} | {row['direction_f1']:.3f} | "
            f"{row['forecast_f1']:.3f} |"
        )
    return "\n".join(lines) + "\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
