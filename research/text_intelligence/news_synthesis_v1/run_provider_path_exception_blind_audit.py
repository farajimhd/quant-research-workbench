from __future__ import annotations

import argparse
from pathlib import Path

from .provider_path_exception_blind_audit import (
    DEFAULT_AUTHORITY,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SUCCESSOR_AUTHORITY,
    finalize_refinement_round_two,
    finalize_successor_authority,
    ingest_compact_reviews,
    ingest_full_reviews,
    prepare,
    prepare_full_confirmation,
    prepare_full_first,
    prepare_refinement_round_two,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the blind provider-path exception audit.")
    parser.add_argument("command", choices=(
        "prepare", "ingest-compact", "prepare-full-first", "ingest-full-first",
        "prepare-full-confirmation", "ingest-full-confirmation", "finalize",
        "prepare-refinement-round-two", "finalize-refinement-round-two",
    ))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--parent-authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR_AUTHORITY)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(output_root=args.output_root)
        print(
            f"{result['audit_version']} prepared | candidates={result['candidates']:,} "
            f"packets={result['packets']:,} output={args.output_root}"
        )
    elif args.command == "ingest-compact":
        if args.staging_root is None:
            parser.error("--staging-root is required for ingest-compact")
        result = ingest_compact_reviews(staging_root=args.staging_root, output_root=args.output_root)
        print(f"compact reviews ingested | articles={result['articles']:,} labels={result['labels']}")
    elif args.command == "prepare-full-first":
        result = prepare_full_first(output_root=args.output_root)
        print(
            f"full-first prepared | articles={result['articles']:,} packets={result['packets']:,} "
            f"oversized={result['oversized_packets']:,}"
        )
    elif args.command == "ingest-full-first":
        if args.staging_root is None:
            parser.error("--staging-root is required for ingest-full-first")
        result = ingest_full_reviews(
            staging_root=args.staging_root, stage="full_first", packet_prefix="PF",
            output_root=args.output_root,
        )
        print(f"full-first ingested | articles={result['articles']:,} labels={result['labels']}")
    elif args.command == "prepare-full-confirmation":
        result = prepare_full_confirmation(output_root=args.output_root)
        print(
            f"full-confirmation prepared | articles={result['articles']:,} "
            f"packets={result['packets']:,} oversized={result['oversized_packets']:,}"
        )
    elif args.command == "ingest-full-confirmation":
        if args.staging_root is None:
            parser.error("--staging-root is required for ingest-full-confirmation")
        result = ingest_full_reviews(
            staging_root=args.staging_root, stage="full_confirmation", packet_prefix="CF",
            output_root=args.output_root,
        )
        print(f"full-confirmation ingested | articles={result['articles']:,} labels={result['labels']}")
    elif args.command == "finalize":
        result = finalize_successor_authority(
            audit_root=args.output_root,
            parent_authority=args.parent_authority,
            successor_authority=args.successor_authority,
        )
        print(
            f"successor finalized | reviewed={result['reviewed_articles']:,} "
            f"corrections={result['correction_counts']} output={args.successor_authority}"
        )
    elif args.command == "prepare-refinement-round-two":
        result = prepare_refinement_round_two()
        print(f"refinement round two prepared | articles={result['articles']:,}")
    else:
        if args.staging_root is None:
            parser.error("--staging-root is required for finalize-refinement-round-two")
        result = finalize_refinement_round_two(staging_root=args.staging_root)
        print(
            f"refinement successor finalized | reviewed={result['reviewed_articles']:,} "
            f"updated={result['updated_primary_rows']:,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
