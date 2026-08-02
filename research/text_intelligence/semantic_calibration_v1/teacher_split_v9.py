from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import stable_json_hash
from .storage import assert_runtime_root, write_json_atomic
from .teacher_paths import DEFAULT_TEACHER_ROOT


SPLIT_VERSION = "news_sol_teacher_grouped_split_v9_1"
SPLIT_TARGETS = {"development": 7_997, "validation": 1_000, "locked_test": 1_000}
_URL_RE = re.compile(r"https?://\S+", re.I)
_MONEY_RE = re.compile(
    r"(?:[$\u20ac\u00a3]|\b(?:usd|cad|eur|gbp)\b)\s*\d[\d,.]*(?:\s*(?:million|billion|m|b))?",
    re.I,
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{2,4})?\b",
    re.I,
)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,:/-]\d+)*\b")
_SPACE_RE = re.compile(r"\s+")


def ensure_grouped_split(
    teacher_root: Path = DEFAULT_TEACHER_ROOT,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    output_root = output_root or teacher_root / "deterministic_v9"
    assert_runtime_root(output_root)
    target = output_root / "grouped_split.json"
    if target.exists():
        manifest = json.loads(target.read_text(encoding="utf-8"))
        if manifest.get("split_version") != SPLIT_VERSION:
            raise RuntimeError(f"Unexpected V9 split version in {target}")
        return manifest

    source_manifest = json.loads((teacher_root / "sample_manifest.json").read_text(encoding="utf-8"))
    label_root = teacher_root / "sol_batch" / "labels"
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw in source_manifest.get("items") or ():
        sample_id = str(raw["sample_id"])
        label_path = label_root / f"{sample_id}.json"
        if not label_path.exists():
            missing.append(sample_id)
            continue
        item = json.loads((teacher_root / "items" / f"{sample_id}.json").read_text(encoding="utf-8"))
        rows.append(_split_row(item))
    if len(rows) != sum(SPLIT_TARGETS.values()):
        raise RuntimeError(f"Expected 9,997 valid teacher rows; found {len(rows):,}")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["group_key"]].append(row)
    assignments = _assign_groups(groups)
    items = []
    for group_key, group_rows in groups.items():
        split = assignments[group_key]
        for row in group_rows:
            items.append({**row, "split": split})
    items.sort(key=lambda row: row["sample_id"])
    counts = Counter(row["split"] for row in items)
    year_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in items:
        year_counts[row["split"]][str(row["calendar_year"])] += 1
    group_leakage = sum(
        len({row["split"] for row in items if row["group_key"] == group_key}) > 1
        for group_key in groups
    )
    manifest = {
        "split_version": SPLIT_VERSION,
        "source_corpus_version": source_manifest.get("corpus_version"),
        "source_selection_sha256": source_manifest.get("selection_sha256"),
        "valid_count": len(items),
        "missing_teacher_labels": missing,
        "targets": SPLIT_TARGETS,
        "counts": dict(counts),
        "group_count": len(groups),
        "largest_group": max(map(len, groups.values()), default=0),
        "group_leakage_count": group_leakage,
        "calendar_year_by_split": {name: dict(values) for name, values in year_counts.items()},
        "items_sha256": stable_json_hash(items),
        "items": items,
    }
    write_json_atomic(target, manifest)
    return manifest


def _split_row(item: Mapping[str, Any]) -> dict[str, Any]:
    publication = item.get("publication") or {}
    timestamp = str(item.get("source_timestamp") or "")
    provider = str(publication.get("provider") or "unknown").casefold()
    template = normalized_headline_template(
        str(publication.get("title") or ""),
        publication.get("provider_tickers") or (),
    )
    group_payload = f"{provider}|{template}"
    group_key = hashlib.sha256(group_payload.encode("utf-8")).hexdigest()
    return {
        "sample_id": str(item["sample_id"]),
        "source_id": str(item["source_id"]),
        "calendar_year": int(timestamp[:4]),
        "provider": provider,
        "headline_template": template,
        "group_key": group_key,
    }


def normalized_headline_template(title: str, tickers: Iterable[str]) -> str:
    value = title.casefold()
    value = _URL_RE.sub(" <url> ", value)
    for ticker in sorted({str(item).casefold() for item in tickers if item}, key=len, reverse=True):
        value = re.sub(rf"(?<![a-z0-9]){re.escape(ticker)}(?![a-z0-9])", " <ticker> ", value)
    value = _MONEY_RE.sub(" <money> ", value)
    value = _PERCENT_RE.sub(" <percent> ", value)
    value = _DATE_RE.sub(" <date> ", value)
    value = _NUMBER_RE.sub(" <number> ", value)
    value = re.sub(r"[^a-z0-9<>%]+", " ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _assign_groups(groups: Mapping[str, list[dict[str, Any]]]) -> dict[str, str]:
    remaining = dict(SPLIT_TARGETS)
    assignments: dict[str, str] = {}
    ordered = sorted(
        groups,
        key=lambda key: (-len(groups[key]), hashlib.sha256(key.encode("ascii")).hexdigest()),
    )
    for group_key in ordered:
        size = len(groups[group_key])
        candidates = sorted(
            remaining,
            key=lambda name: (
                remaining[name] < size,
                -(remaining[name] / SPLIT_TARGETS[name]),
                hashlib.sha256(f"{group_key}|{name}".encode("ascii")).hexdigest(),
            ),
        )
        selected = candidates[0]
        assignments[group_key] = selected
        remaining[selected] -= size
    if any(remaining.values()):
        raise RuntimeError(f"Unable to satisfy exact grouped split targets: {remaining}")
    return assignments
