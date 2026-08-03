from __future__ import annotations

from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .run_render_fresh_acceptance_v2_audits import main as render_main


def main() -> int:
    base = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
    )
    root = base / "news_acceptance_200_v4_reviewed"
    return render_main([
        "--acceptance-root",
        str(root),
        "--evaluation-root",
        str(root / "candidate21_evaluation"),
        "--output-root",
        str(root / "candidate21_article_audits"),
        "--raw-path-map",
        r"D:\market-data=\\DESKTOP-SAAI85T\Workstation-D\market-data",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
