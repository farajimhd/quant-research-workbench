from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .forecast_trigger_eligibility_audit import (
    create_frozen_split,
    compare_eligibility_audits,
    evaluate_audit_partition,
    generate_eligibility_audit,
    generate_audit_packets,
    rebind_frozen_split,
    rescore_cached_predictions,
)


def _population_ids(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("article_ids")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError("Population IDs must be a JSON string list or a frozen partition document")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit News Synthesis forecast-trigger eligibility."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--output-root", type=Path, required=True)
    audit.add_argument("--population-ids", type=Path)
    audit.add_argument("--no-prediction-documents", action="store_true")
    split = subparsers.add_parser("split")
    split.add_argument("--audit-root", type=Path, required=True)
    split.add_argument("--output-root", type=Path, required=True)
    split.add_argument("--sealed-fraction", type=float, default=0.30)
    rebind = subparsers.add_parser("rebind")
    rebind.add_argument("--source-split-root", type=Path, required=True)
    rebind.add_argument("--audit-root", type=Path, required=True)
    rebind.add_argument("--output-root", type=Path, required=True)
    partition = subparsers.add_parser("partition")
    partition.add_argument("--audit-root", type=Path, required=True)
    partition.add_argument("--partition", type=Path, required=True)
    partition.add_argument("--output-root", type=Path, required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--previous-root", type=Path, required=True)
    compare.add_argument("--current-root", type=Path, required=True)
    compare.add_argument("--output-root", type=Path, required=True)
    rescore = subparsers.add_parser("rescore")
    rescore.add_argument("--source-audit-root", type=Path, required=True)
    rescore.add_argument("--output-root", type=Path, required=True)
    rescore.add_argument("--population-ids", type=Path)
    packets = subparsers.add_parser("packets")
    packets.add_argument("--audit-root", type=Path, required=True)
    packets.add_argument("--split-root", type=Path, required=True)
    packets.add_argument("--output-root", type=Path, required=True)
    packets.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.command == "audit":
        manifest = generate_eligibility_audit(
            args.output_root.resolve(),
            population_ids=_population_ids(args.population_ids),
            persist_prediction_documents=not args.no_prediction_documents,
        )
        print(json.dumps({
            "version": manifest["version"],
            "engine_version": manifest["authority"]["engine_version"],
            "population": manifest["population"],
            "issuer_unit_metrics": manifest["issuer_unit_metrics"],
            "article_metrics": manifest["article_metrics"],
            "coverage": manifest["coverage"],
            "engine_failures": manifest["engine_failures"],
        }, indent=2))
        return 0 if not manifest["engine_failures"] else 1
    if args.command == "split":
        manifest = create_frozen_split(
            args.audit_root.resolve(),
            args.output_root.resolve(),
            sealed_fraction=args.sealed_fraction,
        )
    elif args.command == "rebind":
        manifest = rebind_frozen_split(
            args.source_split_root.resolve(),
            args.audit_root.resolve(),
            args.output_root.resolve(),
        )
    elif args.command == "partition":
        manifest = evaluate_audit_partition(
            args.audit_root.resolve(),
            args.partition.resolve(),
            args.output_root.resolve(),
        )
    elif args.command == "compare":
        manifest = compare_eligibility_audits(
            args.previous_root.resolve(),
            args.current_root.resolve(),
            args.output_root.resolve(),
        )
    elif args.command == "rescore":
        manifest = rescore_cached_predictions(
            args.source_audit_root.resolve(),
            args.output_root.resolve(),
            population_ids=_population_ids(args.population_ids),
        )
    else:
        manifest = generate_audit_packets(
            args.audit_root.resolve(),
            args.split_root.resolve(),
            args.output_root.resolve(),
            batch_size=args.batch_size,
        )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
