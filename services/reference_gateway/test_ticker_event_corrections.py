from __future__ import annotations

from types import SimpleNamespace

from services.reference_gateway.ticker_event_corrections import (
    correction_for_entity,
    load_ticker_event_corrections,
    validate_provider_signature,
)


def test_tssi_correction_is_exact_identity_and_signature_bound() -> None:
    corrections = load_ticker_event_corrections()
    entity = SimpleNamespace(
        current_ticker="TSSI",
        composite_figi="BBG000D40Z80",
        share_class_figi="BBG001SMD6B8",
        cik="0001320760",
    )
    correction = correction_for_entity(entity, corrections)

    assert correction is not None
    assert correction.correction_id == "tss-inc-s258-provider-symbol"
    assert correction.canonical_timeline == (("2021-09-23", "TSSI"),)
    validate_provider_signature(
        correction,
        [
            {"date": "2023-11-18", "type": "ticker_change", "ticker_change": {"ticker": "S258"}},
            {"date": "2021-09-23", "type": "ticker_change", "ticker_change": {"ticker": "TSSI"}},
        ],
    )
