from __future__ import annotations

import argparse
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import sha256_json
from .sol_teacher_evaluation import load_json, write_json_atomic
from .sol_teacher_forecast_mismatch_review_collection import CONFIDENCE, STAGES


ISSUE_FAMILY_CONSOLIDATION_VERSION = (
    "news_synthesis_sol_forecast_mismatch_issue_family_consolidation_v1"
)
CONSISTENCY = frozenset(("consistent", "mixed"))
SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
JSON_NAME = "consolidated_issue_families.json"
MARKDOWN_NAME = "consolidated_issue_families.md"


def consolidate_issue_families(
    audit_root: Path,
    input_paths: Sequence[Path],
) -> dict[str, Any]:
    """Validate, combine, and persist reviewed engine-error issue families."""
    audit_root = Path(audit_root)
    source_path = audit_root / "consolidated_mismatch_reviews.json"
    source = load_json(source_path)
    if not isinstance(source, list):
        raise RuntimeError("Consolidated mismatch reviews must be a JSON list")

    engine_rows = _validate_source_rows(source)
    ordered_paths = sorted((Path(path) for path in input_paths), key=lambda p: p.name)
    if not ordered_paths:
        raise RuntimeError("At least one issue-family consolidation input is required")
    names = [path.name for path in ordered_paths]
    if len(names) != len(set(names)):
        raise RuntimeError("Issue-family consolidation input names must be unique")

    input_payloads = [(path, load_json(path)) for path in ordered_paths]
    output = _combine(engine_rows, input_payloads)
    output["authority"] = {
        "consolidated_mismatch_reviews_sha256": sha256_json(source),
        "inputs": [
            {"name": path.name, "sha256": sha256_json(payload)}
            for path, payload in input_payloads
        ],
    }

    write_json_atomic(audit_root / JSON_NAME, output)
    _write_text_atomic(audit_root / MARKDOWN_NAME, _render_markdown(output))
    return output


def _validate_source_rows(source: list[Any]) -> list[dict[str, str]]:
    unit_ids: set[str] = set()
    engine_rows: list[dict[str, str]] = []
    for raw in source:
        if not isinstance(raw, Mapping):
            raise RuntimeError("Mismatch review rows must be JSON objects")
        unit_id = _required_text(raw, "unit_id", "mismatch review row")
        if unit_id in unit_ids:
            raise RuntimeError(f"Duplicate mismatch review unit_id: {unit_id}")
        unit_ids.add(unit_id)
        if str(raw.get("mismatch_verdict") or "") != "engine_error":
            continue
        stage = _required_text(raw, "failure_stage", unit_id)
        issue_family = _required_slug(raw, "issue_family", unit_id)
        if stage not in STAGES:
            raise RuntimeError(f"Invalid engine-error failure stage: {unit_id}")
        engine_rows.append({
            "unit_id": unit_id,
            "failure_stage": stage,
            "issue_family": issue_family,
        })
    return sorted(engine_rows, key=lambda row: row["unit_id"])


def _combine(
    engine_rows: list[dict[str, str]],
    input_payloads: Sequence[tuple[Path, Any]],
) -> dict[str, Any]:
    declared_stages: set[str] = set()
    key_owner: dict[tuple[str, str], str] = {}
    families: dict[str, dict[str, Any]] = {}
    input_totals: dict[str, int] = {}

    for path, payload in input_payloads:
        if not isinstance(payload, Mapping):
            raise RuntimeError(f"Consolidation input must be a JSON object: {path.name}")
        stages = _validate_stages(payload.get("stages"), path.name)
        overlap = declared_stages.intersection(stages)
        if overlap:
            raise RuntimeError(
                f"Overlapping consolidation stages: {sorted(overlap)}"
            )
        declared_stages.update(stages)
        total_units = _nonnegative_int(payload.get("total_units"), f"{path.name} total_units")
        input_totals[path.name] = total_units
        unresolved = payload.get("unresolved")
        if not isinstance(unresolved, list) or unresolved:
            raise RuntimeError(f"Consolidation input has unresolved units: {path.name}")
        raw_families = payload.get("families")
        if not isinstance(raw_families, list) or not raw_families:
            raise RuntimeError(f"Consolidation input has no families: {path.name}")

        declared_input_units = 0
        for raw_family in raw_families:
            family = _validate_family(raw_family, stages, path.name)
            canonical = family["canonical_family"]
            if canonical in families:
                raise RuntimeError(f"Duplicate canonical family: {canonical}")
            for key in family["member_keys"]:
                pair = (key["failure_stage"], key["issue_family"])
                prior = key_owner.get(pair)
                if prior is not None:
                    raise RuntimeError(
                        "Duplicate stage-qualified member key: "
                        f"{pair[0]}/{pair[1]} ({prior}, {canonical})"
                    )
                key_owner[pair] = canonical
            families[canonical] = family
            declared_input_units += family["units"]
        if declared_input_units != total_units:
            raise RuntimeError(
                f"Family unit sum does not match {path.name} total_units"
            )

    source_stages = {row["failure_stage"] for row in engine_rows}
    if declared_stages != source_stages:
        missing = sorted(source_stages - declared_stages)
        extra = sorted(declared_stages - source_stages)
        raise RuntimeError(
            f"Consolidation stage coverage mismatch; missing={missing}, extra={extra}"
        )

    source_counts_by_input: dict[str, int] = {}
    for path, payload in input_payloads:
        stages = set(payload["stages"])
        source_counts_by_input[path.name] = sum(
            row["failure_stage"] in stages for row in engine_rows
        )
    for name, declared in input_totals.items():
        if source_counts_by_input[name] != declared:
            raise RuntimeError(
                f"Authoritative unit total does not match {name}: "
                f"{source_counts_by_input[name]} != {declared}"
            )

    assigned: dict[str, list[dict[str, str]]] = {
        canonical: [] for canonical in families
    }
    missing_keys: set[tuple[str, str]] = set()
    for row in engine_rows:
        pair = (row["failure_stage"], row["issue_family"])
        canonical = key_owner.get(pair)
        if canonical is None:
            missing_keys.add(pair)
        else:
            assigned[canonical].append(row)
    if missing_keys:
        rendered = [f"{stage}/{family}" for stage, family in sorted(missing_keys)]
        raise RuntimeError(f"Unmapped engine-error member keys: {rendered}")

    source_keys = {
        (row["failure_stage"], row["issue_family"]) for row in engine_rows
    }
    extra_keys = sorted(set(key_owner) - source_keys)
    if extra_keys:
        rendered = [f"{stage}/{family}" for stage, family in extra_keys]
        raise RuntimeError(f"Member keys absent from authoritative reviews: {rendered}")

    normalized_families: list[dict[str, Any]] = []
    for canonical, family in families.items():
        members = assigned[canonical]
        if len(members) != family["units"]:
            raise RuntimeError(
                f"Authoritative unit count mismatch for {canonical}: "
                f"{len(members)} != {family['units']}"
            )
        representatives = sorted({row["unit_id"] for row in members})[:5]
        if family["representative_unit_ids"] != representatives:
            raise RuntimeError(
                f"Non-deterministic representative_unit_ids for {canonical}"
            )
        normalized_families.append({
            "canonical_family": canonical,
            "units": len(members),
            "stages": sorted({key["failure_stage"] for key in family["member_keys"]}),
            "member_issue_families": sorted({
                key["issue_family"] for key in family["member_keys"]
            }),
            "member_keys": family["member_keys"],
            "representative_unit_ids": representatives,
            "shared_root_cause": family["shared_root_cause"],
            "generic_fix": family["generic_fix"],
            "confidence": family["confidence"],
            "consistency": family["consistency"],
        })
    normalized_families.sort(
        key=lambda family: (-family["units"], family["canonical_family"])
    )
    if sum(family["units"] for family in normalized_families) != len(engine_rows):
        raise RuntimeError("Combined family units do not match engine-error total")

    return {
        "version": ISSUE_FAMILY_CONSOLIDATION_VERSION,
        "total_units": len(engine_rows),
        "stages": sorted(declared_stages),
        "families": normalized_families,
        "unresolved": [],
    }


def _validate_stages(value: Any, context: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"Invalid stages list: {context}")
    if any(not isinstance(stage, str) or stage not in STAGES for stage in value):
        raise RuntimeError(f"Invalid failure stage: {context}")
    if len(value) != len(set(value)):
        raise RuntimeError(f"Duplicate failure stage: {context}")
    return set(value)


def _validate_family(
    value: Any,
    input_stages: set[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"Family must be a JSON object: {context}")
    canonical = _required_slug(value, "canonical_family", context)
    units = _nonnegative_int(value.get("units"), f"{canonical} units")
    if units == 0:
        raise RuntimeError(f"Family must contain at least one unit: {canonical}")
    confidence = _required_text(value, "confidence", canonical)
    consistency = _required_text(value, "consistency", canonical)
    if confidence not in CONFIDENCE:
        raise RuntimeError(f"Invalid confidence for {canonical}")
    if consistency not in CONSISTENCY:
        raise RuntimeError(f"Invalid consistency for {canonical}")
    root_cause = _required_text(value, "shared_root_cause", canonical)
    generic_fix = _required_text(value, "generic_fix", canonical)

    raw_keys = value.get("member_keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise RuntimeError(f"Missing stage-qualified member_keys for {canonical}")
    pairs: list[tuple[str, str]] = []
    for raw_key in raw_keys:
        if not isinstance(raw_key, Mapping):
            raise RuntimeError(f"Invalid member key for {canonical}")
        stage = _required_text(raw_key, "failure_stage", canonical)
        issue_family = _required_slug(raw_key, "issue_family", canonical)
        if stage not in input_stages:
            raise RuntimeError(f"Member key stage outside input partition: {canonical}")
        pairs.append((stage, issue_family))
    if len(pairs) != len(set(pairs)):
        raise RuntimeError(f"Duplicate member key within {canonical}")
    pairs.sort()

    legacy = value.get("member_issue_families")
    derived_legacy = sorted({issue_family for _, issue_family in pairs})
    if legacy is not None:
        if (
            not isinstance(legacy, list)
            or any(not isinstance(item, str) for item in legacy)
            or len(legacy) != len(set(legacy))
            or sorted(legacy) != derived_legacy
        ):
            raise RuntimeError(
                f"member_issue_families disagrees with member_keys: {canonical}"
            )

    representatives = value.get("representative_unit_ids")
    if (
        not isinstance(representatives, list)
        or not 1 <= len(representatives) <= 5
        or any(not isinstance(unit_id, str) or not unit_id.strip() for unit_id in representatives)
        or len(representatives) != len(set(representatives))
    ):
        raise RuntimeError(f"Invalid representative_unit_ids for {canonical}")

    return {
        "canonical_family": canonical,
        "units": units,
        "member_keys": [
            {"failure_stage": stage, "issue_family": issue_family}
            for stage, issue_family in pairs
        ],
        "representative_unit_ids": representatives,
        "shared_root_cause": root_cause,
        "generic_fix": generic_fix,
        "confidence": confidence,
        "consistency": consistency,
    }


def _required_text(value: Mapping[str, Any], key: str, context: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"Missing {key}: {context}")
    return raw.strip()


def _required_slug(value: Mapping[str, Any], key: str, context: str) -> str:
    slug = _required_text(value, key, context)
    if SLUG_RE.fullmatch(slug) is None:
        raise RuntimeError(f"Invalid {key} slug: {context}")
    return slug


def _nonnegative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"Invalid nonnegative integer: {context}")
    return value


def _render_markdown(output: Mapping[str, Any]) -> str:
    lines = [
        "# Consolidated mismatch issue families",
        "",
        f"Total engine-error units: {output['total_units']}",
        "",
        "| Family | Stages | Units | Confidence | Consistency | Shared root cause | Generic fix |",
        "|---|---|---:|---|---|---|---|",
    ]
    for family in output["families"]:
        cells = (
            family["canonical_family"],
            ", ".join(family["stages"]),
            str(family["units"]),
            family["confidence"],
            family["consistency"],
            family["shared_root_cause"],
            family["generic_fix"],
        )
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return " ".join(str(value).split()).replace("|", r"\|")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and consolidate reviewed mismatch issue families."
    )
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    output = consolidate_issue_families(args.audit_root, args.input)
    print(
        f"wrote {output['total_units']} units across "
        f"{len(output['families'])} families"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
