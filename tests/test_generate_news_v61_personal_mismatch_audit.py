from __future__ import annotations

from scripts.generate_news_v61_personal_mismatch_audit import (
    directory_name,
    pattern_explanation,
    render_audit_file,
    title_pattern_id,
)


def _row(*, gold: str = "ineligible", synthesis: str = "eligible") -> dict:
    return {
        "review_id": "V61P123",
        "source_id": "source-1",
        "published_at_utc": "2025-01-02 03:04:05.000000000",
        "title": "Issuer Raises FY2025 Guidance | Versus Estimate",
        "tickers": ["XYZ"],
        "gold_label": gold,
        "synthesis_label": synthesis,
        "forecast_reasons": ["approved_direct_issuer_guidance_policy"],
        "forecast_policy_ids": ["forecast_trigger:issuer_guidance"],
    }


def test_title_pattern_id_preserves_authoritative_primary_pattern() -> None:
    assert title_pattern_id({"primary_pattern_id": "event.guidance_raise"}) == "event.guidance_raise"
    assert title_pattern_id({"primary_pattern_id": ""}) == "unmatched"


def test_directory_name_is_short_stable_and_collision_resistant() -> None:
    first = directory_name("path", "single_subject > report > issuer")
    second = directory_name("path", "single_subject > report > editorial")

    assert first == directory_name("path", "single_subject > report > issuer")
    assert first != second
    assert len(first) < 80


def test_render_audit_file_has_file_and_row_checkboxes_without_row_evidence() -> None:
    rendered = render_audit_file(
        synthesis_path="single_subject > report > issuer",
        pattern_id="event.guidance_raise",
        gold_label="ineligible",
        rows=[_row()],
    )

    assert "- [ ] All articles should be eligible" in rendered
    assert "- [ ] All articles should be ineligible" in rendered
    assert "- [ ] Mixed" in rendered
    assert "| Wrong | Review ID | Gold label | Published (UTC) | Tickers | Title |" in rendered
    assert "| [ ] | `V61P123` | `ineligible` |" in rendered
    assert "News Synthesis evidence" not in rendered
    assert "Issuer Raises FY2025 Guidance \\| Versus Estimate" in rendered


def test_pattern_explanation_is_explicit_for_unmatched() -> None:
    assert "No deterministic primary title-pattern rule matched" in pattern_explanation("unmatched")
