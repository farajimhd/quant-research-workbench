from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files

from .comparison import load_collection
from .fresh_acceptance_audit import (
    load_gateway_source_evidence,
    render_acceptance_audits,
)
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
    parser.add_argument(
        "--raw-path-map",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help=(
            "Map a stored News Gateway raw-artifact path prefix to the machine "
            "that renders the audit; repeat for multiple roots."
        ),
    )
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo), verbose=True)
    items = load_collection(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=120,
    )
    path_maps = [_parse_path_map(value) for value in args.raw_path_map]
    gateway_evidence = load_gateway_source_evidence(
        client,
        items,
        raw_path_maps=path_maps,
    )
    manifest = render_acceptance_audits(
        items,
        v9_prediction_dir=args.evaluation_root / "v9_predictions",
        v10_prediction_dir=args.evaluation_root / "v10_predictions",
        output_root=args.output_root,
        evaluation_path=args.evaluation_root / "evaluation.json",
        gateway_evidence=gateway_evidence,
    )
    print(
        f"READY | articles={manifest['article_count']:,} "
        f"v9_wrong={manifest['articles_with_any_v9_mismatch']:,} "
        f"v10_wrong={manifest['articles_with_any_v10_mismatch']:,} "
        f"index={args.output_root / 'INDEX.md'}",
        flush=True,
    )
    return 0


def _parse_path_map(value: str) -> tuple[str, str]:
    source, separator, target = value.partition("=")
    if not separator or not source.strip() or not target.strip():
        raise argparse.ArgumentTypeError(
            f"invalid --raw-path-map {value!r}; expected SOURCE=TARGET"
        )
    return source.strip(), target.strip()


if __name__ == "__main__":
    raise SystemExit(main())
