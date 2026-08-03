from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

from research.mlops.clickhouse import ClickHouseHttpClient

from .fresh_acceptance import _copy_exact
from .comparison import load_collection
from .fresh_acceptance_v2 import (
    ACCEPTANCE_VERSION as V2_ACCEPTANCE_VERSION,
    AcceptanceBuildResult,
    AcceptanceRoundContract,
    build_acceptance_round,
)
from .schema import ANNOTATION_VERSION_V3, stable_json_hash
from .storage import (
    annotation_directory,
    assert_runtime_root,
    read_json,
    write_json_atomic,
)


ACCEPTANCE_VERSION = "news_semantic_fresh_acceptance_v3"
ACCEPTANCE_SEED = "news-fresh-acceptance-100-v3-20260802"
SAMPLE_ID_START = 1_201
LOCKED_SPLIT = "fresh_acceptance_v3"


def build_acceptance_sample(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    human_1100_root: Path,
    second_acceptance_root: Path,
    teacher_root: Path,
    report: Callable[[str], None] | None = None,
) -> AcceptanceBuildResult:
    """Build N1201-N1300 while proving disjointness from all prior supervision."""
    contract = AcceptanceRoundContract(
        collection_version=ACCEPTANCE_VERSION,
        sampling_seed=ACCEPTANCE_SEED,
        sample_id_start=SAMPLE_ID_START,
        locked_split=LOCKED_SPLIT,
        reviewer_label="Third fresh acceptance review",
        prior_human_authorities=(
            (human_1100_root, 1_100, "human-1100"),
            (second_acceptance_root, 100, V2_ACCEPTANCE_VERSION),
        ),
        teacher_root=teacher_root,
    )
    return build_acceptance_round(client, root, contract=contract, report=report)


def build_combined_human_authority_v3(
    *, original_root: Path, acceptance_roots: tuple[Path, ...], combined_root: Path
) -> Path:
    """Materialize the certified 1,300-article human authority."""
    assert_runtime_root(combined_root)
    roots = (original_root, *acceptance_roots)
    expected = (1_000, *(100 for _ in acceptance_roots))
    collections = tuple(
        load_collection(root, annotation_version=ANNOTATION_VERSION_V3)
        for root in roots
    )
    counts = tuple(len(values) for values in collections)
    if counts != expected:
        raise RuntimeError(f"combined human authority counts {counts} != {expected}")
    all_items = tuple(item for values in collections for item in values)
    source_ids = [str(item.blinded["source_id"]) for item in all_items]
    if len(set(source_ids)) != len(all_items):
        raise RuntimeError("combined human authority contains overlapping source identities")
    manifests = tuple(read_json(root / "sample_manifest.json") for root in roots)
    component_contract = {
        "components": [
            {
                "root_name": root.name,
                "manifest_sha256": str(manifest.get("sample_manifest_sha256") or ""),
                "annotation_set_sha256": stable_json_hash(sorted(
                    (item.sample_id, str(item.truth.get("annotation_sha256") or ""))
                    for item in items
                )),
                "count": len(items),
            }
            for root, manifest, items in zip(roots, manifests, collections, strict=True)
        ],
        "source_ids_sha256": stable_json_hash(sorted(source_ids)),
    }
    rebuilding = (combined_root / "sample_manifest.json").exists()
    for root, items in zip(roots, collections, strict=True):
        for item in items:
            _copy_exact(
                root / "blinded_articles" / f"{item.sample_id}.json",
                combined_root / "blinded_articles" / f"{item.sample_id}.json",
            )
            _copy_exact(
                annotation_directory(root, ANNOTATION_VERSION_V3) / f"{item.sample_id}.json",
                annotation_directory(combined_root, ANNOTATION_VERSION_V3) / f"{item.sample_id}.json",
                allow_review_update=rebuilding,
            )
    manifest = {
        "sample_version": "news_semantic_ground_truth_1300_v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": len(all_items),
        "component_contract": component_contract,
        "items": [row for manifest in manifests for row in manifest["items"]],
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(combined_root / "sample_manifest.json", manifest)
    sealed = {
        "sample_version": manifest["sample_version"],
        "component_contract": component_contract,
        "items": [
            row for root in roots
            for row in read_json(root / "sealed" / "v5_comparison_and_splits.json")["items"]
        ],
    }
    sealed["sealed_selection_sha256"] = stable_json_hash(sealed)
    write_json_atomic(combined_root / "sealed" / "v5_comparison_and_splits.json", sealed)
    return combined_root
