from __future__ import annotations

import argparse
from pathlib import Path

from .provider_context_evaluation import (
    DEFAULT_CORRECTED_AUTHORITY_ROOT,
    DEFAULT_OUTPUT_ROOT,
    run_evaluation,
)
from .provider_filter_analysis import DEFAULT_METADATA_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the provider-context router on corrected 2025-2026 labels.")
    parser.add_argument("--authority-root", type=Path, default=DEFAULT_CORRECTED_AUTHORITY_ROOT)
    parser.add_argument("--metadata-root", type=Path, default=DEFAULT_METADATA_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    report = run_evaluation(
        authority_root=args.authority_root,
        metadata_root=args.metadata_root,
        output_root=args.output_root,
    )
    overall = report["overall"]
    print(
        f"{report['evaluation_version']} complete | articles={overall['articles']:,} "
        f"context_only={overall['context_only']:,} false_rejections="
        f"{overall['context_only_eligible_false_rejections']:,} output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
