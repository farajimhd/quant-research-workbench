from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import torch
from scipy import sparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.text_intelligence.news_synthesis_v1.deepfm_serving import (
    COLUMN_TRANSFORM_VERSION,
    SERVING_CONTRACT_VERSION,
    DeepFMServingRelease,
)
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import iter_jsonl
from research.text_intelligence.news_synthesis_v1.structured_metadata_rf_pre_holdout import _holdout_matrix
from research.text_intelligence.news_synthesis_v1.structured_tfidf_mlp_pre_holdout import (
    _torch_csr,
    apply_column_multipliers,
)
from research.text_intelligence.news_synthesis_v1.structured_tfidf_rf_pre_holdout import _combined_matrix
from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import _load_example_inputs
from research.text_intelligence.llm_issuer_labeling_v3.pipeline import DEFAULT_AUDIT_ROOT, EXAMPLE_PATH
from research.text_intelligence.llm_issuer_labeling_v3.prompt import build_system_prompt, load_example_bank


DEFAULT_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
RELEASE_ID = "news-deepfm-pre-august-v1-scaling-repair-v2"
# Sparse CPU/GPU kernels are not bit-identical; this is tight enough to detect
# semantic transform drift (the production defect changed scores by ~1.0).
REPLAY_TOLERANCE = 5e-6
MOVER_TITLE = re.compile(r"^\d+\s+.+\bstocks?\s+moving\s+in\b", re.IGNORECASE)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _paths(runtime_root: Path) -> dict[str, Path]:
    news_root = runtime_root / "news_synthesis_v1"
    return {
        "parent": news_root / "structured_metadata_rf_v1",
        "rf": news_root / "structured_tfidf_rf_2025_through_2026_aug13_to_august_holdout_v1",
        "deepfm": news_root / "structured_tfidf_deepfm_2025_through_2026_aug13_to_august_holdout_v1",
        "holdout": news_root / "forecast_eligibility_august_2026_temporal_holdout_v1",
    }


def _artifact(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path), "sha256": digest(path)}


def build_manifest(runtime_root: Path, threshold: float) -> dict[str, Any]:
    roots = _paths(runtime_root)
    parent, rf, deepfm, holdout = (
        roots["parent"], roots["rf"], roots["deepfm"], roots["holdout"]
    )
    return {
        "contract_version": SERVING_CONTRACT_VERSION,
        "release_id": RELEASE_ID,
        "status": "promoted",
        "threshold": threshold,
        "threshold_authority": "development-selected pre-August validation threshold",
        "column_transform": {
            "version": COLUMN_TRANSFORM_VERSION,
            "operation": "multiply",
            "semantics": "inverse_max_abs_column_multiplier",
        },
        "artifacts": {
            "feature_contract": _artifact(parent / "FEATURE_CONTRACT.json"),
            "category_catalog": _artifact(parent / "CATEGORY_CATALOG_2010_2025.csv"),
            "tfidf_vectorizer": _artifact(rf / "TFIDF_VECTORIZER.joblib"),
            "column_multiplier": _artifact(deepfm / "COLUMN_SCALE.npy"),
            "model": _artifact(deepfm / "MODEL.pt"),
            "frozen_holdout_predictions": _artifact(deepfm / "PREDICTIONS_HOLDOUT.jsonl"),
            "frozen_holdout_validation": _artifact(holdout / "VALIDATION.json"),
        },
    }


def _probabilities(
    release: DeepFMServingRelease,
    matrix: sparse.csr_matrix,
    *,
    device: torch.device,
    batch_size: int,
) -> np.ndarray:
    model = release.model.to(device).eval()
    result: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, matrix.shape[0], batch_size):
            tensor = _torch_csr(matrix[start:start + batch_size], device)
            result.append(torch.sigmoid(model(tensor)).cpu().numpy())
    return np.concatenate(result).astype(np.float64, copy=False)


def _canary_positions(rows: list[dict[str, Any]], expected: np.ndarray) -> list[int]:
    positions = {
        index for index, row in enumerate(rows)
        if MOVER_TITLE.search(str(row.get("title") or ""))
    }
    for eligible in (True, False):
        candidates = [
            index for index, score in enumerate(expected)
            if (score >= 0.5) == eligible
        ]
        ordered = sorted(candidates, key=lambda index: (expected[index], index))
        if ordered:
            positions.update(ordered[index] for index in np.linspace(
                0, len(ordered) - 1, num=min(32, len(ordered)), dtype=int,
            ))
    return sorted(positions)


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "source_timestamp": str(row["published_at_text"]),
        "text": str(row.get("rendered_text") or ""),
    }


def validate_manifest(manifest_path: Path, runtime_root: Path) -> dict[str, Any]:
    roots = _paths(runtime_root)
    release = DeepFMServingRelease(manifest_path, device="cpu")
    contract = json.loads((roots["parent"] / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    structured, rows, _truth = _holdout_matrix(
        holdout_root=roots["holdout"], parent_root=roots["parent"], contract=contract,
    )
    vectorizer = joblib.load(roots["rf"] / "TFIDF_VECTORIZER.joblib")
    text = vectorizer.transform(str(row["rendered_text"]) for row in rows)
    matrix = apply_column_multipliers(
        _combined_matrix(structured, text), release.column_multiplier,
    )
    stored = list(iter_jsonl(roots["deepfm"] / "PREDICTIONS_HOLDOUT.jsonl"))
    expected_by_id = {
        str(row["source_id"]): float(row["eligible_probability"]) for row in stored
    }
    if len(expected_by_id) != len(rows):
        raise ValueError("frozen holdout prediction population changed")
    expected = np.asarray(
        [expected_by_id[str(row["source_id"])] for row in rows], dtype=np.float64,
    )
    cpu_batched = _probabilities(
        release, matrix, device=torch.device("cpu"), batch_size=2048,
    )
    cpu_error = float(np.max(np.abs(cpu_batched - expected)))
    if cpu_error > REPLAY_TOLERANCE:
        raise ValueError(f"frozen holdout replay differs: max_abs_error={cpu_error}")

    canaries = _canary_positions(rows, expected)
    single = []
    mover_count = 0
    for index in canaries:
        row = rows[index]
        if MOVER_TITLE.search(str(row.get("title") or "")):
            mover_count += 1
        scored = release.score(_source_row(row), ticker_history=row, market_cap=row)
        single.append(float(scored["eligible_probability"]))
    single_error = float(np.max(np.abs(np.asarray(single) - expected[canaries])))
    if single_error > REPLAY_TOLERANCE:
        raise ValueError(f"batch-size-one serving replay differs: max_abs_error={single_error}")

    gpu = {"available": torch.cuda.is_available(), "max_abs_error_vs_cpu": None}
    if torch.cuda.is_available():
        gpu_values = _probabilities(
            release, matrix, device=torch.device("cuda"), batch_size=2048,
        )
        gpu_error = float(np.max(np.abs(gpu_values - cpu_batched)))
        gpu["max_abs_error_vs_cpu"] = gpu_error
        if gpu_error > REPLAY_TOLERANCE:
            raise ValueError(f"GPU/CPU replay differs: max_abs_error={gpu_error}")
        release.model.to(torch.device("cpu"))

    return {
        "status": "passed",
        "contract_version": SERVING_CONTRACT_VERSION,
        "release_id": release.release_id,
        "release_hash": release.release_hash,
        "frozen_holdout": {
            "rows": len(rows),
            "expected_rows": len(stored),
            "cpu_batched_max_abs_error": cpu_error,
            "tolerance": REPLAY_TOLERANCE,
        },
        "batch_size_one_canaries": {
            "rows": len(canaries),
            "mover_template_rows": mover_count,
            "max_abs_error": single_error,
        },
        "gpu_parity": gpu,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def _publish_issuer_review_prompt(target_directory: Path) -> dict[str, str]:
    prompt_path = target_directory / "issuer_review_system_prompt_v1.txt"
    metadata_path = target_directory / "issuer_review_system_prompt_v1.json"
    bank = load_example_bank(EXAMPLE_PATH)
    catalog_path = DEFAULT_AUDIT_ROOT / "audit_source_catalog.jsonl"
    prompt = build_system_prompt(bank, _load_example_inputs(catalog_path, bank)) + "\n"
    if prompt_path.exists() and prompt_path.read_text(encoding="utf-8") != prompt:
        raise FileExistsError(f"existing promoted prompt differs: {prompt_path}")
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    metadata = {
        "contract_version": "news_issuer_review_prompt_v1",
        "prompt_sha256": digest(prompt_path),
        "example_bank": {"path": str(EXAMPLE_PATH), "sha256": digest(EXAMPLE_PATH)},
        "example_catalog": {"path": str(catalog_path), "sha256": digest(catalog_path)},
    }
    _write_json(metadata_path, metadata)
    return {"issuer_review_prompt": str(prompt_path), "issuer_review_prompt_hash": metadata["prompt_sha256"]}


def promote(runtime_root: Path, threshold: float) -> dict[str, Any]:
    target = runtime_root / "serving" / "news_forecast_funnel_v1" / "release_v2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(runtime_root, threshold)
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    candidate = target.with_suffix(".candidate.json")
    if target.exists() and target.read_text(encoding="utf-8") != rendered:
        raise FileExistsError(f"existing promoted release differs: {target}")
    candidate.write_text(rendered, encoding="utf-8", newline="\n")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            sys.executable, str(Path(__file__).resolve()),
            "--validate-manifest", str(candidate),
            "--runtime-root", str(runtime_root),
        ],
        cwd=REPO_ROOT, env=environment, check=True,
    )
    report_path = candidate.with_suffix(".validation.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed":
        raise ValueError("fresh-process release validation did not pass")
    if not target.exists():
        os.replace(candidate, target)
    else:
        candidate.unlink()
    final_report = target.with_suffix(".validation.json")
    report["release_hash"] = digest(target)
    _write_json(final_report, report)
    report_path.unlink()
    prompt = _publish_issuer_review_prompt(target.parent)
    return {"status": "promoted", "manifest": str(target), **report, **prompt}


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Promote and replay-validate the frozen News DeepFM release")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--threshold", type=float, default=0.38)
    parser.add_argument("--validate-manifest", type=Path)
    args = parser.parse_args(argv)
    if args.validate_manifest:
        report = validate_manifest(args.validate_manifest, args.runtime_root)
        _write_json(args.validate_manifest.with_suffix(".validation.json"), report)
    else:
        report = promote(args.runtime_root, args.threshold)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
