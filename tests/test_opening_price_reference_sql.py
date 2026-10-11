"""Opt-in SELECT-only SQL qualification on generated database rows."""
from contextlib import closing
import os

import pytest

from pipelines.market_sip.events import opening_price_reference_producer as p
from pipelines.market_sip.events.market_day_sql import literal
from src.market_engine import opening_price_reference as c
from test_opening_price_reference_producer import source


def bars_fixture():
    return """(SELECT 'prior' AS build_id,toDate('2026-07-31') AS session_date,
      'ABC' AS ticker,toUUID('00000000-0000-0000-0000-000000000001') AS attempt_id,
      toUInt32(1000) AS resolution_ms,
      toUInt32(arrayElement([34199,34200,57599,57600,70000],number+1)) AS bucket_index,
      toUInt8(1) AS price_valid,toUInt64(if(number=2,200000,900000+number)) AS close_int
      FROM numbers(5))"""


def split_fixture():
    return """(SELECT 'symbol' AS symbol_id,if(number=3,'foreign','listing') AS listing_id,
      'security' AS security_id,toDate(if(number=2,'2026-07-31','2026-08-03')) AS execution_date,
      toFloat64(1) AS split_from,toFloat64(if(number=0,2,100)) AS split_to,
      toDateTime64(if(number=1,'2026-08-03 08:00:01','2026-08-03 07:00:00'),6,'UTC') AS inserted_at,
      repeat('4',64) AS source_content_sha256 FROM numbers(4))"""


@pytest.mark.skipif(os.environ.get("QUALIFY_OPENING_SQL") != "1",
                    reason="Explicit read-only database qualification")
def test_real_sql_filters_regular_session_identity_and_opening_known_splits():
    from src.backend.backtest_v3_clients import v3_client
    src = source()
    queries = []
    with closing(v3_client("read")) as client:
        class SyntheticReader:
            def execute(self, query):
                assert query.startswith("SELECT")
                query = query.replace("FROM arte.bars_v1", "FROM " + bars_fixture())
                query = query.replace("FROM q_live.market_stock_split_v1 FINAL", "FROM " + split_fixture())
                assert "arte." not in query and "q_live." not in query
                queries.append(query)
                return client.execute(query)
        records, splits = p.materialize_opening_reference(SyntheticReader(), src,
            attempt_id="00000000-0000-0000-0000-000000000002")
    assert len(queries) == 2
    assert len(records) == len(splits) == 1
    assert records[0]["prior_close_int"] == 200000
    assert records[0]["adjusted_prior_close"] == 10.
    assert records[0]["split_price_factor"] == .5


@pytest.mark.skipif(os.environ.get("QUALIFY_OPENING_SQL") != "1",
                    reason="Explicit read-only database qualification")
def test_real_clickhouse_scalar_wire_roundtrip_matches_reference_hash():
    import json
    from src.backend.backtest_v3_clients import v3_client
    from test_opening_price_reference import project
    row, splits = project()
    with closing(v3_client("read")) as client:
        for table, record in ((c.REFERENCE, row), (c.SPLIT, splits[0])):
            wire = p._wire_records(table, [record])[0]
            expressions = [f"CAST({'NULL' if wire[name] is None else literal(wire[name])},"
                f"{literal(kind)}) AS {name}" for name, kind in table.columns]
            query = "SELECT " + ",".join(expressions) + " FORMAT JSONEachRow"
            restored = c.restore_record(table, json.loads(client.execute(query).strip()))
            assert restored == record
            assert c.content_hash(restored) == c.content_hash(record)
