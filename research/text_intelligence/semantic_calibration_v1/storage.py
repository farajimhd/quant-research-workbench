from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import ANNOTATION_VERSION, stable_json_hash, validate_annotation


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
    result = validate_annotation(record, expected_item=item)
    if not result.valid:
        raise ValueError("invalid annotation: " + ", ".join(result.errors))
    record["annotation_version"] = ANNOTATION_VERSION
    record["annotation_sha256"] = stable_json_hash(record)
    target = root / "annotations" / f"{sample_id}.json"
    if target.exists():
        existing = read_json(target)
        if existing == record:
            return str(record["annotation_sha256"])
        raise FileExistsError(
            f"annotation already exists for {sample_id}; revisions require a new review round"
        )
    write_json_atomic(target, record)
    refresh_annotation_state(root)
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
        if unit.get("evidence_spans"):
            continue
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


def refresh_annotation_state(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "sample_manifest.json")
    expected = [str(item["sample_id"]) for item in manifest["items"]]
    completed = sorted(path.stem for path in (root / "annotations").glob("*.json"))
    unexpected = sorted(set(completed) - set(expected))
    state = {
        "sample_version": manifest["sample_version"],
        "sample_manifest_sha256": manifest["sample_manifest_sha256"],
        "expected": len(expected),
        "completed": len(set(completed) & set(expected)),
        "remaining": len(set(expected) - set(completed)),
        "unexpected": unexpected,
    }
    write_json_atomic(root / "annotation_state.json", state)
    return state
