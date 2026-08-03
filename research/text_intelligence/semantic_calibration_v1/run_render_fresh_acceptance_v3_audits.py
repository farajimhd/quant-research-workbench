from __future__ import annotations

from pathlib import Path
import sys

from research.mlops.paths import MLOpsPathConfig

from .run_render_fresh_acceptance_v2_audits import main as render_main


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    root = (
        runtime
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_acceptance_100_v3"
    )
    defaults = [
        "--acceptance-root",
        str(root),
        "--evaluation-root",
        str(root / "evaluation"),
        "--output-root",
        str(root / "article_audits"),
    ]
    return render_main([*defaults, *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
