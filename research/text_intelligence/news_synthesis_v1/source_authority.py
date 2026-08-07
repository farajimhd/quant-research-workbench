from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SOURCE_COLLECTION_NAMES = (
    "news_1300_v1",
    "news_acceptance_200_v4_reviewed_v2",
    "news_acceptance_500_v5_reviewed",
)


@dataclass(frozen=True, slots=True)
class SourceAuthorityConfig:
    collection_roots: tuple[Path, ...]
    runtime_root: Path
    expected_articles: int = 2_000


def default_source_authority_config() -> SourceAuthorityConfig:
    runtime_root = Path(os.environ.get("QW_MLOPS_ROOT", "D:/TradingML")) / "runtimes"
    calibration_root = runtime_root / "text_intelligence" / "semantic_calibration_v1"
    return SourceAuthorityConfig(
        collection_roots=tuple(
            calibration_root / name for name in SOURCE_COLLECTION_NAMES
        ),
        runtime_root=runtime_root,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def discover_pairs(
    collection_roots: Sequence[Path],
) -> list[tuple[Path, Path, str]]:
    pairs: list[tuple[Path, Path, str]] = []
    for root in collection_roots:
        annotation_root = root / "annotations_v3"
        article_root = root / "blinded_articles"
        if not annotation_root.is_dir() or not article_root.is_dir():
            raise FileNotFoundError(f"Missing gold directories under {root}")
        annotations = {path.stem: path for path in annotation_root.glob("*.json")}
        articles = {path.stem: path for path in article_root.glob("*.json")}
        if annotations.keys() != articles.keys():
            missing_articles = sorted(annotations.keys() - articles.keys())
            missing_annotations = sorted(articles.keys() - annotations.keys())
            raise RuntimeError(
                f"Unpaired gold files under {root}: missing_articles={missing_articles[:5]} "
                f"missing_annotations={missing_annotations[:5]}"
            )
        pairs.extend(
            (annotations[key], articles[key], root.name) for key in sorted(annotations)
        )
    return pairs
