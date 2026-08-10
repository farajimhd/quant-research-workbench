from __future__ import annotations

import argparse
import json
from pathlib import Path

from .consolidated_gold_evaluator import (
    certify_source_catalog,
    compare_audits,
    create_frozen_split,
    evaluate_inference,
    run_inference,
    validate_audit,
    validate_frozen_split,
    write_source_requirements,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split and evaluate the consolidated News Synthesis gold authority."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    split = subparsers.add_parser("split")
    split.add_argument("--runtime-root", type=Path, required=True)
    split.add_argument("--consolidated-root", type=Path, required=True)
    split.add_argument("--output-root", type=Path, required=True)
    split.add_argument("--development-test-fraction", type=float, default=0.20)
    split.add_argument("--seed", default="news-synthesis-consolidated-gold-audit-v1")

    validate_split = subparsers.add_parser("validate-split")
    validate_split.add_argument("--runtime-root", type=Path, required=True)
    validate_split.add_argument("--consolidated-root", type=Path, required=True)
    validate_split.add_argument("--split-root", type=Path, required=True)

    requirements = subparsers.add_parser("requirements")
    requirements.add_argument("--runtime-root", type=Path, required=True)
    requirements.add_argument("--split-root", type=Path, required=True)
    requirements.add_argument("--output-path", type=Path, required=True)

    certify_sources = subparsers.add_parser("certify-sources")
    certify_sources.add_argument("--runtime-root", type=Path, required=True)
    certify_sources.add_argument("--source-catalog", type=Path, required=True)
    certify_sources.add_argument(
        "--source-artifact",
        type=Path,
        action="append",
        required=True,
        help="Repeat for every authoritative runtime source artifact used.",
    )
    certify_sources.add_argument("--output-manifest", type=Path, required=True)

    infer = subparsers.add_parser("infer")
    infer.add_argument("--runtime-root", type=Path, required=True)
    infer.add_argument("--split-root", type=Path, required=True)
    infer.add_argument("--consolidated-root", type=Path, required=True)
    infer.add_argument("--source-catalog", type=Path, required=True)
    infer.add_argument("--source-catalog-manifest", type=Path, required=True)
    infer.add_argument("--output-root", type=Path, required=True)
    infer.add_argument("--partition", choices=("audit", "development_test", "final_test"), default="audit")
    infer.add_argument("--workers", type=int, default=1)
    infer.add_argument(
        "--release-test-partition",
        action="store_true",
        help="Explicitly permit inference on a held-out test partition.",
    )

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--runtime-root", type=Path, required=True)
    evaluate.add_argument("--split-root", type=Path, required=True)
    evaluate.add_argument("--consolidated-root", type=Path, required=True)
    evaluate.add_argument("--inference-root", type=Path, required=True)
    evaluate.add_argument("--output-root", type=Path, required=True)
    evaluate.add_argument("--partition", choices=("audit", "development_test", "final_test"), default="audit")
    evaluate.add_argument("--mismatch-chunk-size", type=int, default=25)
    evaluate.add_argument(
        "--release-test-partition",
        action="store_true",
        help="Explicitly permit evaluation of a held-out test partition.",
    )

    validate = subparsers.add_parser("validate-audit")
    validate.add_argument("--runtime-root", type=Path, required=True)
    validate.add_argument("--audit-root", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--runtime-root", type=Path, required=True)
    compare.add_argument("--previous-root", type=Path, required=True)
    compare.add_argument("--current-root", type=Path, required=True)
    compare.add_argument("--output-root", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "split":
        report = create_frozen_split(
            args.consolidated_root,
            args.output_root,
            runtime_root=args.runtime_root,
            development_test_fraction=args.development_test_fraction,
            seed=args.seed,
        )
    elif args.command == "validate-split":
        report = validate_frozen_split(
            args.consolidated_root,
            args.split_root,
            runtime_root=args.runtime_root,
        )
    elif args.command == "requirements":
        report = write_source_requirements(
            args.split_root,
            args.output_path,
            runtime_root=args.runtime_root,
        )
    elif args.command == "certify-sources":
        report = certify_source_catalog(
            args.source_catalog,
            args.source_artifact,
            args.output_manifest,
            runtime_root=args.runtime_root,
        )
    elif args.command == "infer":
        report = run_inference(
            args.split_root,
            args.consolidated_root,
            args.source_catalog,
            args.source_catalog_manifest,
            args.output_root,
            runtime_root=args.runtime_root,
            partition=args.partition,
            allow_test=args.release_test_partition,
            workers=args.workers,
        )
    elif args.command == "evaluate":
        report = evaluate_inference(
            args.split_root,
            args.consolidated_root,
            args.inference_root,
            args.output_root,
            runtime_root=args.runtime_root,
            partition=args.partition,
            allow_test=args.release_test_partition,
            mismatch_chunk_size=args.mismatch_chunk_size,
        )
    elif args.command == "validate-audit":
        report = validate_audit(args.audit_root, runtime_root=args.runtime_root)
    else:
        report = compare_audits(
            args.previous_root,
            args.current_root,
            args.output_root,
            runtime_root=args.runtime_root,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
