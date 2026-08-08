from __future__ import annotations

import json
from pathlib import Path

import pytest

from .sol_teacher_forecast_mismatch_issue_family_consolidation import (
    JSON_NAME,
    MARKDOWN_NAME,
    consolidate_issue_families,
)


def test_consolidates_deterministically_and_writes_both_outputs(tmp_path: Path) -> None:
    audit_root, first, second = _fixture(tmp_path)

    output = consolidate_issue_families(audit_root, [second, first])
    json_bytes = (audit_root / JSON_NAME).read_bytes()
    markdown_bytes = (audit_root / MARKDOWN_NAME).read_bytes()

    assert output["total_units"] == 3
    assert output["stages"] == ["aggregation", "entity_binding"]
    assert [family["canonical_family"] for family in output["families"]] == [
        "binding_scope",
        "aggregation_weighting",
    ]
    assert output["families"][0]["representative_unit_ids"] == ["S1::AAA", "S2::BBB"]
    assert [item["name"] for item in output["authority"]["inputs"]] == [
        second.name,
        first.name,
    ]
    assert b"scope \\| attribution" in markdown_bytes

    rerun = consolidate_issue_families(audit_root, [first, second])
    assert rerun == output
    assert (audit_root / JSON_NAME).read_bytes() == json_bytes
    assert (audit_root / MARKDOWN_NAME).read_bytes() == markdown_bytes


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_mapping", "Unmapped"),
        ("duplicate_key", "Duplicate stage-qualified"),
        ("wrong_total", "unit sum"),
        ("duplicate_family", "Duplicate canonical"),
        ("nonempty_unresolved", "unresolved"),
        ("invalid_slug", "slug"),
        ("blank_text", "shared_root_cause"),
        ("wrong_representatives", "Non-deterministic"),
    ],
)
def test_rejects_invalid_consolidations_before_writing(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    audit_root, first, second = _fixture(tmp_path)
    payload = _read(first)
    if mutation == "missing_mapping":
        payload["families"][0]["member_keys"] = [
            {"failure_stage": "entity_binding", "issue_family": "scope_leak"}
        ]
        payload["families"][0]["member_issue_families"] = ["scope_leak"]
    elif mutation == "duplicate_key":
        payload["families"].append(dict(payload["families"][0], canonical_family="other_family"))
        payload["total_units"] = 4
    elif mutation == "wrong_total":
        payload["total_units"] = 99
    elif mutation == "duplicate_family":
        other = _read(second)
        other["families"][0]["canonical_family"] = payload["families"][0]["canonical_family"]
        _write(second, other)
    elif mutation == "nonempty_unresolved":
        payload["unresolved"] = ["S9::ZZZ"]
    elif mutation == "invalid_slug":
        payload["families"][0]["canonical_family"] = "Bad Family"
    elif mutation == "blank_text":
        payload["families"][0]["shared_root_cause"] = " "
    elif mutation == "wrong_representatives":
        payload["families"][0]["representative_unit_ids"] = ["S2::BBB", "S1::AAA"]
    _write(first, payload)

    with pytest.raises(RuntimeError, match=message):
        consolidate_issue_families(audit_root, [first, second])
    assert not (audit_root / JSON_NAME).exists()
    assert not (audit_root / MARKDOWN_NAME).exists()


def test_rejects_overlapping_stage_partitions(tmp_path: Path) -> None:
    audit_root, first, second = _fixture(tmp_path)
    payload = _read(second)
    payload["stages"] = ["entity_binding"]
    payload["families"][0]["member_keys"][0]["failure_stage"] = "entity_binding"
    _write(second, payload)

    with pytest.raises(RuntimeError, match="Overlapping"):
        consolidate_issue_families(audit_root, [first, second])


def test_rejects_stage_coverage_gap_and_extra_member_key(tmp_path: Path) -> None:
    audit_root, first, second = _fixture(tmp_path)
    payload = _read(second)
    payload["families"][0]["member_keys"].append(
        {"failure_stage": "aggregation", "issue_family": "invented_issue"}
    )
    payload["families"][0]["member_issue_families"].append("invented_issue")
    _write(second, payload)

    with pytest.raises(RuntimeError, match="absent from authoritative"):
        consolidate_issue_families(audit_root, [first, second])


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    audit_root = tmp_path / "audit"
    audit_root.mkdir()
    _write(audit_root / "consolidated_mismatch_reviews.json", [
        {
            "unit_id": "S2::BBB",
            "mismatch_verdict": "engine_error",
            "failure_stage": "entity_binding",
            "issue_family": "role_swap",
        },
        {
            "unit_id": "S0::GOLD",
            "mismatch_verdict": "gold_question",
            "failure_stage": "entity_binding",
            "issue_family": "ignored_gold",
        },
        {
            "unit_id": "S1::AAA",
            "mismatch_verdict": "engine_error",
            "failure_stage": "entity_binding",
            "issue_family": "scope_leak",
        },
        {
            "unit_id": "S3::CCC",
            "mismatch_verdict": "engine_error",
            "failure_stage": "aggregation",
            "issue_family": "duplicate_weight",
        },
    ])
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    first = input_root / "identity.json"
    second = input_root / "aggregation.json"
    _write(first, {
        "stages": ["entity_binding"],
        "total_units": 2,
        "families": [
            _family(
                "binding_scope",
                2,
                [
                    ("entity_binding", "role_swap"),
                    ("entity_binding", "scope_leak"),
                ],
                ["S1::AAA", "S2::BBB"],
                root="scope | attribution",
            )
        ],
        "unresolved": [],
    })
    _write(second, {
        "stages": ["aggregation"],
        "total_units": 1,
        "families": [
            _family(
                "aggregation_weighting",
                1,
                [("aggregation", "duplicate_weight")],
                ["S3::CCC"],
            )
        ],
        "unresolved": [],
    })
    return audit_root, first, second


def _family(
    canonical: str,
    units: int,
    keys: list[tuple[str, str]],
    representatives: list[str],
    *,
    root: str = "Shared root cause.",
) -> dict[str, object]:
    return {
        "canonical_family": canonical,
        "units": units,
        "member_issue_families": sorted({issue for _, issue in keys}),
        "member_keys": [
            {"failure_stage": stage, "issue_family": issue}
            for stage, issue in keys
        ],
        "representative_unit_ids": representatives,
        "shared_root_cause": root,
        "generic_fix": "Apply one generic fix.",
        "confidence": "high",
        "consistency": "consistent",
    }


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
