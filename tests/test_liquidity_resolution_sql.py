"""Opt-in read-only ClickHouse arithmetic qualification; never installs/writes."""
from datetime import date, datetime, timedelta
from contextlib import closing
from zoneinfo import ZoneInfo
import asyncio
import json
import os

import pytest

from pipelines.market_sip.events import liquidity_resolution_sql as sql


def unit():
    return sql.LiquiditySourceUnit("synthetic-source", date(2026, 8, 3), "AAPL",
        "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
        "a" * 64, "b" * 64)


def test_products_are_shared_scalar_ssd_contracts_and_scope_is_pinned():
    for table in sql.TABLES:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert not any(kind.startswith(("JSON", "Array", "Tuple", "Map", "Object")) for _, kind in table.columns)
    with pytest.raises(ValueError):
        sql.LiquiditySourceUnit("x", date(2026, 8, 3), "AAPL' OR 1=1", "bad", "bad", "a" * 64, "b" * 64)
    with pytest.raises(ValueError):
        sql.source_scope(unit(), qualifier="unsafe.")


def liquidity_fixture():
    base = "toUnixTimestamp64Micro(toDateTime64('2026-08-03 14:00:00',6,'America/New_York'))"
    columns = dict(build_id="'synthetic-source'", session_date="toDate('2026-08-03')",
        ticker="'AAPL'", attempt_id="toUUID('00000000-0000-0000-0000-000000000001')",
        resolution_ms="toUInt32(100)", bucket_index="toUInt32(504000+number)",
        first_event_us=f"toUInt64({base}+number*100000+1)",
        last_event_us=f"toUInt64({base}+number*100000+50000)",
        open_int="toUInt64(100000+number*10)", high_int="toUInt64(100010+number*10)",
        low_int="toUInt64(99990+number*10)", close_int="toUInt64(100005+number*10)",
        volume="toFloat64(if(number<9,100,0))", notional="toFloat64(if(number<9,1000+number*.1+.005,0))",
        execution_volume="toFloat64(if(number<9,10,0))",
        execution_notional="toFloat64(if(number<9,100+number*.01+.0005,0))",
        price_valid="toUInt8(number<9)", extremes_valid="toUInt8(number<9)", volume_valid="toUInt8(number<9)",
        quote_timestamp_us=f"toUInt64({base}+number*100000+40000)",
        bid_int="toUInt64(99999+number*10)", ask_int="toUInt64(100001+number*10)",
        bid_size="toFloat64(100+number)", ask_size="toFloat64(200+number)",
        cumulative_volume="toFloat64((least(number,8)+1)*100)",
        cumulative_notional="toFloat64((least(number,8)+1)*1000+.05*least(number,8)*(least(number,8)+1)+.005*(least(number,8)+1))",
        cumulative_execution_volume="toFloat64((least(number,8)+1)*10)",
        cumulative_execution_notional="toFloat64((least(number,8)+1)*100+.005*least(number,8)*(least(number,8)+1)+.0005*(least(number,8)+1))",
        execution_vwap="toFloat64((100000+least(number,8)*5+.5)/10000)",
        spread="toFloat64(.0002)", quote_valid="toUInt8(1)")
    for name in sql.COUNTERS:
        columns[name] = "toUInt32(0)"
    columns.update(event_count="toUInt32(if(number<9,3,1))", source_trade_count="toUInt32(if(number<9,2,0))",
        trade_count="toUInt32(if(number<9,2,0))", quote_event_count="toUInt32(1)")
    return "(SELECT " + ",".join(f"{value} AS {name}" for name, value in columns.items()) + " FROM numbers(10))"


def price_fixture():
    return """(SELECT 'synthetic-source' AS source_build_id,toDate('2026-08-03') AS session_date,
      'AAPL' AS ticker,toUUID('00000000-0000-0000-0000-000000000001') AS source_attempt_id,
      toUUID('00000000-0000-0000-0000-000000000002') AS derivation_attempt_id,
      toUInt32(504000+intDiv(number,2)) AS bucket_index,
      toUInt64(100000+intDiv(number,2)*10+number%2) AS price_int,
      toFloat64(5) AS execution_volume FROM numbers(18))"""


@pytest.mark.skipif(os.environ.get("QUALIFY_LIQUIDITY_SQL") != "1", reason="Explicit read-only database qualification")
def test_real_clickhouse_aggregation_and_real_broker_partial_fill():
    from src.backend.backtest_v3_clients import v3_client
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
    from src.trading_runtime.runtime import RunMode
    from src.trading_runtime.ibkr_schema import OrderRequest
    from src.backend.backtest_liquidity_resolution import (
        CertifiedLiquidityResolution, liquidity_window_sql, read_liquidity_window,
    )
    attempt = "00000000-0000-0000-0000-000000000003"
    liquidity = sql.liquidity_select_sql(unit(), attempt).replace(
        "FROM arte.liquidity_100ms_v1 s", "FROM " + liquidity_fixture() + " s")
    prices = sql.price_select_sql(unit(), attempt).replace(
        "FROM arte.liquidity_execution_price_100ms_v1 s", "FROM " + price_fixture() + " s")
    assert "arte." not in liquidity and "arte." not in prices
    with closing(v3_client("read")) as client:
        rows = [json.loads(line) for line in client.execute(liquidity + " FORMAT JSONEachRow").splitlines()]
        levels = [json.loads(line) for line in client.execute(prices + " FORMAT JSONEachRow").splitlines()]
        parity = sql.bucket_parity_sql(unit(), attempt).replace(
            f"FROM arte.{sql.LIQUIDITY.name}", "FROM (" + liquidity + ") liquid_source").replace(
            f"FROM arte.{sql.PRICE.name}", "FROM (" + prices + ") price_source")
        assert "arte." not in parity
        assert client.execute(parity + " FORMAT JSONEachRow").strip() == ""
        bad = parity.replace("toFloat64(5) AS execution_volume", "toFloat64(if(number=0,4,5)) AS execution_volume")
        rejected = [json.loads(line) for line in client.execute(bad + " FORMAT JSONEachRow").splitlines()]
        assert rejected == [{"bucket_index": 50400}]
        certificate = CertifiedLiquidityResolution(unit(), attempt, "c" * 64, "d" * 64, 1, 18, 90.)
        query = liquidity_window_sql(certificate, first_bucket=50400, stop_bucket=50402).replace(
            f"FROM arte.{sql.LIQUIDITY.name}", "FROM (" + liquidity + ") liquid_source").replace(
            f"FROM arte.{sql.PRICE.name}", "FROM (" + prices + ") price_source")
        assert "arte." not in query
        class SyntheticReader:
            def execute(self, supplied):
                assert supplied.startswith("SELECT")
                return client.execute(query + " FORMAT JSONEachRow")
        window = read_liquidity_window(SyntheticReader(), certificate,
                                      first_bucket=50400, stop_bucket=50402)
    assert len(rows) == 1 and len(levels) == 18
    row = rows[0]
    assert row["bucket_index"] == 50400 and int(row["event_count"]) == 28
    assert int(row["open_int"]) == 100000 and int(row["close_int"]) == 100085
    assert int(row["high_int"]) == 100090 and int(row["low_int"]) == 99990
    assert row["volume"] == row["cumulative_volume"] == 900
    assert row["execution_volume"] == row["cumulative_execution_volume"] == 90
    assert int(row["ask_int"]) == 100091 and row["ask_size"] == 209
    assert row["execution_vwap"] == pytest.approx(10.00405)
    assert sum(level["execution_volume"] for level in levels) == 90
    row = window[0]
    start = datetime(2026, 8, 3, 14, tzinfo=ZoneInfo("America/New_York"))
    async def match():
        broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(liquidity_resolution_ms=1000),
            mode=RunMode.BACKTEST, initial_time=start, fixed_bar_mode=True)
        await broker.initialize()
        await broker.place_orders("TEST", [OrderRequest(acctId="TEST", conid=1, cOID="partial",
            ticker="AAPL", orderType="LMT", side="BUY", quantity=12, price=10.0001)])
        executions = await broker.on_liquidity_bar(row, at=start + timedelta(seconds=1))
        # Existing25% passive participation and whole-share quantization:
        # floor(10 eligible shares*.25)=2. Passive fills use the order limit.
        assert sum(ex.size for ex in executions) == 2
        assert executions[0].price == pytest.approx(10.0001)
        assert sum(ex.commission for ex in executions) == 1
        assert broker.position_quantity("TEST", 1, "AAPL") == 2
    asyncio.run(match())
