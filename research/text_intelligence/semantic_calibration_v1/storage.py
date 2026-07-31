from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import (
    ANNOTATION_VERSION,
    ANNOTATION_VERSION_V1,
    stable_json_hash,
    validate_annotation,
)


def assert_runtime_root(path: Path) -> None:
    resolved = path.resolve()
    repository = Path(__file__).resolve().parents[3]
    if resolved == repository or repository in resolved.parents:
        raise ValueError(f"generated calibration data cannot be stored in repository: {resolved}")
    lowered = str(resolved).replace("/", "\\").casefold()
    if "\\runtimes\\" not in lowered and not lowered.endswith("\\runtimes"):
        raise ValueError(f"calibration output must live below a machine runtimes root: {resolved}")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_annotation(
    root: Path,
    annotation: Mapping[str, Any],
) -> str:
    assert_runtime_root(root)
    sample_id = str(annotation.get("sample_id") or "")
    item_path = root / "blinded_articles" / f"{sample_id}.json"
    if not item_path.exists():
        raise FileNotFoundError(f"unknown sample item {sample_id}: {item_path}")
    item = read_json(item_path)
    record = materialize_evidence_spans(dict(annotation), item)
    record.pop("annotation_sha256", None)
    result = validate_annotation(record, expected_item=item)
    if not result.valid:
        raise ValueError("invalid annotation: " + ", ".join(result.errors))
    annotation_version = str(record.get("annotation_version") or "")
    record["annotation_sha256"] = stable_json_hash(record)
    target = annotation_directory(root, annotation_version) / f"{sample_id}.json"
    if target.exists():
        existing = read_json(target)
        if existing == record:
            return str(record["annotation_sha256"])
        raise FileExistsError(
            f"annotation already exists for {sample_id}; revisions require a new review round"
        )
    write_json_atomic(target, record)
    refresh_annotation_state(root, annotation_version=annotation_version)
    return str(record["annotation_sha256"])


def materialize_evidence_spans(
    annotation: dict[str, Any],
    item: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve reviewer-selected quotes to exact immutable text positions.

    This is provenance bookkeeping, not semantic automation. Ambiguous or
    absent quotes fail rather than silently selecting an occurrence.
    """
    publication = item.get("publication") or {}
    rendered = item.get("rendered_product") or {}
    sources: list[tuple[str, int | None, str]] = [
        ("title", None, str(publication.get("title") or "")),
        ("teaser", None, str(publication.get("teaser") or "")),
        ("rendered_text", None, str(rendered.get("text") or "")),
    ]
    sources.extend(
        (
            "source_lane",
            int(lane.get("source_ordinal") or 0),
            str(lane.get("text") or ""),
        )
        for lane in item.get("source_lanes") or ()
    )
    units = annotation.get("issuer_units") or []
    for unit in units:
        if not unit.get("evidence_spans"):
            spans: list[dict[str, Any]] = []
            for raw_quote in unit.get("evidence_quotes") or ():
                quote = str(raw_quote)
                match = unique_preferred_match(quote, sources)
                if match is None:
                    raise ValueError(
                        f"evidence quote is absent or ambiguous for {unit.get('ticker')}: {quote!r}"
                    )
                field, source_ordinal, start = match
                span = {
                    "source_field": field,
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                }
                if source_ordinal is not None:
                    span["source_ordinal"] = source_ordinal
                spans.append(span)
            unit["evidence_spans"] = spans
        for opinion in unit.get("analyst_opinions") or ():
            if opinion.get("evidence_spans"):
                continue
            quotes = tuple(
                dict.fromkeys(
                    (
                        *(opinion.get("evidence_quotes") or ()),
                        *(opinion.get("reasoning_quotes") or ()),
                    )
                )
            )
            opinion_spans: list[dict[str, Any]] = []
            for raw_quote in quotes:
                quote = str(raw_quote)
                match = unique_preferred_match(quote, sources)
                if match is None:
                    raise ValueError(
                        "analyst evidence quote is absent or ambiguous for "
                        f"{unit.get('ticker')}: {quote!r}"
                    )
                field, source_ordinal, start = match
                span = {
                    "source_field": field,
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                }
                if source_ordinal is not None:
                    span["source_ordinal"] = source_ordinal
                opinion_spans.append(span)
            opinion["evidence_spans"] = opinion_spans
    return annotation


def unique_preferred_match(
    quote: str,
    sources: Iterable[tuple[str, int | None, str]],
) -> tuple[str, int | None, int] | None:
    if not quote:
        return None
    for field, source_ordinal, text in sources:
        first = text.find(quote)
        if first < 0:
            continue
        if text.find(quote, first + 1) >= 0:
            return None
        return field, source_ordinal, first
    return None


def annotation_directory(root: Path, annotation_version: str) -> Path:
    if annotation_version == ANNOTATION_VERSION_V1:
        return root / "annotations"
    if annotation_version == ANNOTATION_VERSION:
        return root / "annotations_v2"
    raise ValueError(f"unsupported annotation version: {annotation_version}")


def refresh_annotation_state(
    root: Path,
    *,
    annotation_version: str = ANNOTATION_VERSION,
) -> dict[str, Any]:
    manifest = read_json(root / "sample_manifest.json")
    expected = [str(item["sample_id"]) for item in manifest["items"]]
    completed = sorted(
        path.stem for path in annotation_directory(root, annotation_version).glob("*.json")
    )
    unexpected = sorted(set(completed) - set(expected))
    state = {
        "sample_version": manifest["sample_version"],
        "sample_manifest_sha256": manifest["sample_manifest_sha256"],
        "annotation_version": annotation_version,
        "expected": len(expected),
        "completed": len(set(completed) & set(expected)),
        "remaining": len(set(expected) - set(completed)),
        "unexpected": unexpected,
    }
    state_name = (
        "annotation_state.json"
        if annotation_version == ANNOTATION_VERSION_V1
        else "annotation_state_v2.json"
    )
    write_json_atomic(root / state_name, state)
    return state
