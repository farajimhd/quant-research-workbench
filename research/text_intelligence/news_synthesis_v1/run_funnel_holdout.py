from __future__ import annotations

import argparse
from pathlib import Path

from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import clickhouse_client

from .funnel_holdout import DEFAULT_CORRECTED_LABELS, DEFAULT_OUTPUT_ROOT, freeze_fresh_holdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze a prediction-blind post-authority News Synthesis holdout.")
    parser.add_argument("--corrected-labels", type=Path, default=DEFAULT_CORRECTED_LABELS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--sample-size", type=int, default=1_000)
    args = parser.parse_args(argv)
    client = clickhouse_client()
    try:
        manifest = freeze_fresh_holdout(
            client,
            corrected_labels=args.corrected_labels,
            output_root=args.output_root,
            sample_size=args.sample_size,
        )
    finally:
        client.close()
    source = manifest["source_window"]
    print(
        f"{manifest['holdout_version']} sealed | population={source['population_rows']:,} "
        f"sample={source['sample_rows']:,} output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
