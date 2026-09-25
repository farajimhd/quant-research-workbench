"""Producer sidecar must retain the certified market-day execution filter."""
from datetime import date

from pipelines.market_sip.events import market_day_sql as market
from pipelines.market_sip.events import liquidity_execution_price_sql as prices


DAY = date(2026, 8, 18)
SOURCE_ATTEMPT = "00000000-0000-0000-0000-000000000001"
PRICE_ATTEMPT = "00000000-0000-0000-0000-000000000002"


def test_price_product_is_normalized_month_partitioned_and_ssd_only():
    definitions = prices.ddl()
    assert len(definitions) == 2
    assert all("PARTITION BY toYYYYMM(session_date)" in sql for sql in definitions)
    assert all("storage_policy='live_market_ssd'" in sql for sql in definitions)
    assert all("JSON" not in sql and "Array(" not in sql for sql in definitions)
    assert "bucket_index,price_int" in definitions[0]
    assert "source_attempt_id UUID, derivation_attempt_id UUID" in definitions[0]


def test_price_query_matches_core_execution_eligibility_and_uses_only_canonical_events():
    rules = []
    sidecar = prices.insert_sql(
        "source-build", DAY, "ABCD", SOURCE_ATTEMPT, PRICE_ATTEMPT, rules)
    base = market.broker_sql(
        "arte", "source-build", DAY, "ABCD", SOURCE_ATTEMPT, rules)
    for condition in (
        "bitAnd(event_meta,",
        "AND price_int>0",
        "AND size>0 AND isFinite(size) AS usable",
        "toUInt8(usable AND",
        "argMaxIf(tuple(sip_timestamp_us,secondary_int,price_int,",
        "intDiv(sip_timestamp_us-q.1,1000)<=1000",
        "price_int+greatest(q.2,q.3,price_int)*1e-9>=q.2",
        "price_int<=q.3+greatest(q.2,q.3,price_int)*1e-9",
    ):
        assert condition in sidecar and condition in base
    assert "merge('market_sip_compact'" in sidecar
    assert "file(" not in sidecar.lower()
    assert "FROM classified WHERE execution_valid=1" in sidecar
    assert "GROUP BY bucket_index,price_int" in sidecar
