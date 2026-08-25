from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_current_news_intelligence_docs_match_deepfm_only_authority() -> None:
    implementation = (
        REPO_ROOT / "docs" / "2026-08-24-news-forecast-funnel-implementation.md"
    ).read_text(encoding="utf-8")
    services = (
        REPO_ROOT / "docs" / "services" / "AI_INFERENCE_SERVICES.md"
    ).read_text(encoding="utf-8")
    text_intelligence = (
        REPO_ROOT / "services" / "text-intelligence" / "README.md"
    ).read_text(encoding="utf-8")

    current_docs = "\n".join((implementation, services, text_intelligence))
    stale_contract_phrases = (
        "deterministic final-ineligible gate",
        "Final-ineligible documents stop there",
        "applies News Synthesis V1 eligibility",
        "V1 eligibility + active-session + QMD price gate",
    )

    for phrase in stale_contract_phrases:
        assert phrase not in current_docs

    assert "DeepFM is the sole live forecast-eligibility authority" in implementation
    assert "Synthesis cannot gate DeepFM" in services
    assert "sole live forecast-eligibility authority" in text_intelligence
    assert "TEXT_INTELLIGENCE_FORECAST_ELIGIBILITY_THRESHOLD" in text_intelligence
