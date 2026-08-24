from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.text_intelligence.news_synthesis_v1.deepfm_serving import (
    SERVING_CONTRACT_VERSION,
    DeepFMServingRelease,
)
from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import _load_example_inputs
from research.text_intelligence.llm_issuer_labeling_v3.pipeline import DEFAULT_AUDIT_ROOT, EXAMPLE_PATH
from research.text_intelligence.llm_issuer_labeling_v3.prompt import build_system_prompt, load_example_bank


DEFAULT_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote the frozen News forecast DeepFM release")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--threshold", type=float, default=0.38)
    args = parser.parse_args()
    news_root = args.runtime_root / "news_synthesis_v1"
    structured = news_root / "structured_metadata_rf_v1"
    rf = news_root / "structured_tfidf_rf_2025_through_2026_aug13_to_august_holdout_v1"
    deepfm = news_root / "structured_tfidf_deepfm_2025_through_2026_aug13_to_august_holdout_v1"
    paths = {
        "feature_contract": structured / "FEATURE_CONTRACT.json",
        "category_catalog": structured / "CATEGORY_CATALOG_2010_2025.csv",
        "tfidf_vectorizer": rf / "TFIDF_VECTORIZER.joblib",
        "column_scale": deepfm / "COLUMN_SCALE.npy",
        "model": deepfm / "MODEL.pt",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest = {
        "contract_version": SERVING_CONTRACT_VERSION,
        "release_id": "news-deepfm-pre-august-v1",
        "status": "promoted",
        "threshold": args.threshold,
        "threshold_authority": "development-selected pre-August validation threshold",
        "artifacts": {
            name: {"path": str(path), "sha256": digest(path)}
            for name, path in paths.items()
        },
    }
    target = args.runtime_root / "serving" / "news_forecast_funnel_v1" / "release.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != rendered:
        raise FileExistsError(f"existing promoted release differs: {target}")
    target.write_text(rendered, encoding="utf-8", newline="\n")
    release = DeepFMServingRelease(target, device="cpu")
    prompt_path = target.parent / "issuer_review_system_prompt_v1.txt"
    prompt_meta_path = target.parent / "issuer_review_system_prompt_v1.json"
    bank = load_example_bank(EXAMPLE_PATH)
    catalog_path = DEFAULT_AUDIT_ROOT / "audit_source_catalog.jsonl"
    prompt = build_system_prompt(bank, _load_example_inputs(catalog_path, bank)) + "\n"
    if prompt_path.exists() and prompt_path.read_text(encoding="utf-8") != prompt:
        raise FileExistsError(f"existing promoted prompt differs: {prompt_path}")
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    prompt_meta = {
        "contract_version": "news_issuer_review_prompt_v1",
        "prompt_sha256": digest(prompt_path),
        "example_bank": {"path": str(EXAMPLE_PATH), "sha256": digest(EXAMPLE_PATH)},
        "example_catalog": {"path": str(catalog_path), "sha256": digest(catalog_path)},
    }
    prompt_meta_path.write_text(json.dumps(prompt_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": "promoted", "manifest": str(target), "release_hash": release.release_hash,
        "issuer_review_prompt": str(prompt_path), "issuer_review_prompt_hash": prompt_meta["prompt_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
