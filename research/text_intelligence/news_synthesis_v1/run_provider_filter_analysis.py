from __future__ import annotations

import argparse
from pathlib import Path

from .provider_filter_analysis import (
    DEFAULT_AUTHORITY_ROOT,
    DEFAULT_METADATA_ROOT,
    DEFAULT_OUTPUT_ROOT,
    run_analysis,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze causal provider, text, time, and ticker-history strengths for forecast-noise filtering."
    )
    parser.add_argument("--authority-root", type=Path, default=DEFAULT_AUTHORITY_ROOT)
    parser.add_argument("--metadata-root", type=Path, default=DEFAULT_METADATA_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    report = run_analysis(
        authority_root=args.authority_root,
        metadata_root=args.metadata_root,
        output_root=args.output_root,
    )
    print(
        f"{report['analysis_version']} complete | articles={report['population']['analysis_rows']:,} "
        f"features={report['feature_count']:,} candidates={report['candidate_count']:,} "
        f"output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
