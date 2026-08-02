from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .comparison import load_collection
from .fresh_acceptance_audit import render_acceptance_audits
from .schema import ANNOTATION_VERSION_V3


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Render one human/V9/V10 Markdown audit per fresh article."
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_100_v1"
    )
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=base / "news_acceptance_100_v1" / "evaluation",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_100_v1" / "article_audits",
    )
    args = parser.parse_args(argv)
    items = load_collection(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    manifest = render_acceptance_audits(
        items,
        v9_prediction_dir=args.evaluation_root / "v9_predictions",
        v10_prediction_dir=args.evaluation_root / "v10_predictions",
        output_root=args.output_root,
        evaluation_path=args.evaluation_root / "evaluation.json",
    )
    print(
        f"READY | articles={manifest['article_count']:,} "
        f"v9_wrong={manifest['articles_with_any_v9_mismatch']:,} "
        f"v10_wrong={manifest['articles_with_any_v10_mismatch']:,} "
        f"index={args.output_root / 'INDEX.md'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
