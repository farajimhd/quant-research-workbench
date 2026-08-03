from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig

from .comparison import CollectionItem, evaluate_predictions, load_collection
from .deterministic_v9_config import DETERMINISTIC_V9_VERSION
from .fresh_acceptance_audit import GatewaySourceEvidence, load_gateway_source_evidence
from .fresh_acceptance_v9_audit import render_v9_acceptance_audits
from .run_deterministic_news_v6 import _headline
from .run_deterministic_news_v9 import (
    generate_v9_predictions,
    load_v9_issuer_authority,
    prediction_is_current,
)
from .schema import ANNOTATION_VERSION_V3, stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


CONTRACT = "news_human_2000_candidate44_audit_collection_v1"
EXPECTED_SAMPLE_IDS = tuple(f"N{index:04d}" for index in range(1, 2_001))
DEFAULT_RAW_PATH_MAP = (
    r"D:\market-data=\\DESKTOP-SAAI85T\Workstation-D\market-data"
)


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo), verbose=True)
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description=(
            "Render one evaluator-authoritative Markdown collection for all "
            "2,000 certified human News articles."
        )
    )
    parser.add_argument(
        "--human-1300-root", type=Path, default=base / "news_1300_v1"
    )
    parser.add_argument(
        "--reviewed-200-root",
        type=Path,
        default=base / "news_acceptance_200_v4_reviewed_v2",
    )
    parser.add_argument(
        "--reviewed-500-root",
        type=Path,
        default=base / "news_acceptance_500_v5_reviewed",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_human_2000_candidate44_audits",
    )
    parser.add_argument("--source-batch-size", type=int, default=100)
    parser.add_argument(
        "--raw-path-map",
        action="append",
        default=None,
        help="Map a retained raw-artifact prefix as SOURCE=TARGET.",
    )
    parser.add_argument(
        "--allow-missing-gateway-source",
        action="store_true",
        help=(
            "Retain the frozen source lanes and mark unavailable raw Gateway "
            "evidence explicitly instead of failing."
        ),
    )
    args = parser.parse_args(argv)
    if args.source_batch_size <= 0:
        parser.error("--source-batch-size must be positive")
    assert_runtime_root(args.output_root)

    items = _load_certified_items(
        (args.human_1300_root, args.reviewed_200_root, args.reviewed_500_root)
    )
    evaluation_root = args.output_root / "evaluation"
    prediction_dir = evaluation_root / "v9_predictions"
    _ensure_current_predictions(items, prediction_dir)
    report = evaluate_predictions(
        items,
        prediction_dir=prediction_dir,
        canonical_concepts=True,
    )
    evaluation = {
        "contract": CONTRACT,
        "articles": len(items),
        "deterministic_version": DETERMINISTIC_V9_VERSION,
        "source_roots": [
            str(args.human_1300_root),
            str(args.reviewed_200_root),
            str(args.reviewed_500_root),
        ],
        "sample_id_sha256": stable_json_hash([item.sample_id for item in items]),
        "v9": report,
        "headline": {"v9": _headline(report)},
    }
    write_json_atomic(evaluation_root / "evaluation.json", evaluation)

    path_maps = [
        _parse_path_map(value)
        for value in (args.raw_path_map or [DEFAULT_RAW_PATH_MAP])
    ]
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=120,
    )
    evidence = _load_source_evidence_bounded(
        client,
        items,
        batch_size=args.source_batch_size,
        raw_path_maps=path_maps,
        allow_missing=args.allow_missing_gateway_source,
    )
    manifest = render_v9_acceptance_audits(
        items,
        prediction_dir=prediction_dir,
        output_root=args.output_root,
        evaluation_path=evaluation_root / "evaluation.json",
        gateway_evidence=evidence,
    )
    print(json.dumps(evaluation["headline"], indent=2), flush=True)
    print(
        "READY | "
        f"articles={manifest['article_count']:,} "
        f"gateway_available={manifest['gateway_source_available']:,} "
        f"gateway_unavailable={manifest['gateway_source_unavailable']:,} "
        f"index={args.output_root / 'INDEX.md'}",
        flush=True,
    )
    return 0


def _load_certified_items(roots: tuple[Path, ...]) -> tuple[CollectionItem, ...]:
    items = tuple(
        item
        for root in roots
        for item in load_collection(root, annotation_version=ANNOTATION_VERSION_V3)
    )
    by_sample_id = {item.sample_id: item for item in items}
    if len(items) != 2_000 or tuple(sorted(by_sample_id)) != EXPECTED_SAMPLE_IDS:
        missing = sorted(set(EXPECTED_SAMPLE_IDS) - set(by_sample_id))
        unexpected = sorted(set(by_sample_id) - set(EXPECTED_SAMPLE_IDS))
        raise RuntimeError(
            "certified human authority is not exactly N0001-N2000: "
            f"rows={len(items):,} unique={len(by_sample_id):,} "
            f"missing={missing[:5]} unexpected={unexpected[:5]}"
        )
    source_ids = [str(item.blinded["source_id"]) for item in items]
    if len(set(source_ids)) != len(source_ids):
        raise RuntimeError("certified human authority contains duplicate source IDs")
    return tuple(by_sample_id[sample_id] for sample_id in EXPECTED_SAMPLE_IDS)


def _ensure_current_predictions(
    items: tuple[CollectionItem, ...], prediction_dir: Path
) -> None:
    current = all(
        (target := prediction_dir / f"{item.sample_id}.json").is_file()
        and prediction_is_current(read_json(target))
        for item in items
    )
    if current:
        print(f"V9 HUMAN reuse={len(items):,}/{len(items):,}", flush=True)
        return
    generate_v9_predictions(
        items,
        prediction_dir,
        issuer_resolver=load_v9_issuer_authority(),
    )


def _load_source_evidence_bounded(
    client: ClickHouseHttpClient,
    items: tuple[CollectionItem, ...],
    *,
    batch_size: int,
    raw_path_maps: list[tuple[str, str]],
    allow_missing: bool,
) -> dict[str, GatewaySourceEvidence]:
    output: dict[str, GatewaySourceEvidence] = {}
    for offset in range(0, len(items), batch_size):
        batch = items[offset : offset + batch_size]
        batch_evidence = load_gateway_source_evidence(
            client,
            batch,
            raw_path_maps=raw_path_maps,
            allow_missing=allow_missing,
        )
        overlap = set(output) & set(batch_evidence)
        if overlap:
            raise RuntimeError(
                "duplicate source evidence across batches: "
                + ", ".join(sorted(overlap)[:5])
            )
        output.update(batch_evidence)
        print(
            f"SOURCE {min(offset + len(batch), len(items)):,}/{len(items):,}",
            flush=True,
        )
    if len(output) != len(items):
        raise RuntimeError(
            f"source evidence coverage mismatch: {len(output):,}/{len(items):,}"
        )
    return output


def _parse_path_map(value: str) -> tuple[str, str]:
    source, separator, target = value.partition("=")
    if not separator or not source.strip() or not target.strip():
        raise argparse.ArgumentTypeError(
            f"invalid --raw-path-map {value!r}; expected SOURCE=TARGET"
        )
    return source.strip(), target.strip()


if __name__ == "__main__":
    raise SystemExit(main())
