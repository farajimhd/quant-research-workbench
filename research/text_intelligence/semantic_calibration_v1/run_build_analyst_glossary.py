from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .analyst_glossary import persist_analyst_glossary


def default_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a text-only analyst and research-firm glossary from V2 reviews."
    )
    parser.add_argument("--runtime-root", type=Path, default=default_root())
    args = parser.parse_args(argv)
    glossary = persist_analyst_glossary(args.runtime_root)
    print(
        "ANALYST GLOSSARY | "
        f"analysts={len(glossary['analysts'])} "
        f"firms={len(glossary['firms'])} "
        f"affiliations={len(glossary['observed_affiliations'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
