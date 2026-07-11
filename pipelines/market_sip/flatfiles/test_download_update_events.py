from __future__ import annotations

from pipelines.market_sip.flatfiles.download_update_events import clickhouse_price_int, trade_raw_row_to_event


def test_clickhouse_price_int_uses_clickhouse_half_even_rounding() -> None:
    assert clickhouse_price_int("0.76905") == 7690


def test_trade_raw_row_to_event_matches_omex_half_tick_insert() -> None:
    row = {
        "ticker": "OMEX",
        "conditions": "37",
        "correction": "0",
        "exchange": "4",
        "participant_timestamp": "1745522406849832396",
        "price": "0.76905",
        "sequence_number": "6898024",
        "sip_timestamp": "1745522406850095260",
        "size": "3",
        "tape": "3",
    }
    token_maps = {"trade_conditions": {0: 60, 37: 96}}

    event = trade_raw_row_to_event(row, token_maps)

    assert event is not None
    assert event["ticker"] == "OMEX"
    assert event["event_type"] == 1
    assert event["event_meta"] == 19
    assert event["sip_timestamp_us"] == 1745522406850095
    assert event["sequence_number"] == 6898024
    assert event["price_primary_int"] == 7690
    assert event["price_secondary_int"] == 0
    assert event["size_primary"] == 3.0
    assert event["size_secondary"] == 0.0
    assert event["exchange_primary"] == 4
    assert event["exchange_secondary"] == 0
    assert [event[f"condition_token_{idx}"] for idx in range(1, 6)] == [96, 60, 60, 60, 60]
    assert event["event_date"] == "2025-04-24"
