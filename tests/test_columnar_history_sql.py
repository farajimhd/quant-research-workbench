"""Explicit SELECT-only qualification of ClickHouse scalar wire semantics."""
from contextlib import closing
import json
import os

import numpy as np
import pytest

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.market_sip.events.columnar_history_producer import wire_records, value_insert_projection
from src.market_engine import columnar_history_product as c
from test_columnar_history_publication import source, CODE


@pytest.mark.skipif(os.environ.get("QUALIFY_HISTORY_SQL") != "1",
                    reason="Explicit read-only database qualification")
def test_real_clickhouse_float32_nullable_wire_preserves_feature_bits_and_hashes():
    from src.backend.backtest_v3_clients import v3_client
    scope = source().scope("00000000-0000-0000-0000-000000000002", CODE)
    values = [None, float(np.float32(.1)), float(np.nextafter(np.float32(.1), np.float32(1))),
              float(np.float32(-0.)), float(np.finfo(np.float32).tiny), float(np.finfo(np.float32).max)]
    with closing(v3_client("read")) as client:
        for value in values:
            row = {**scope.prefix(), "field_id": 326, "first_ordinal": 0,
                "stop_ordinal": scope.clock_count, "value": value,
                "value_bits": None if value is None else int(np.float32(value).view(np.uint32)),
                "valid": int(value is not None)}
            wire = wire_records(c.VALUE, [row])[0]
            expressions = [("CAST(NULL,'Nullable(Float32)') AS value" if value is None else
                f"reinterpretAsFloat32(toUInt32({row['value_bits']})) AS value") if n == "value" else
                f"CAST({'NULL' if wire[n] is None else literal(wire[n])},{literal(kind)}) AS {n}"
                for n, kind in c.VALUE.columns]
            response = client.execute("SELECT " + ",".join(expressions) + " FORMAT JSONEachRow")
            try:
                restored = c.restore_record(c.VALUE, json.loads(response.strip()))
            except ValueError as exc:
                raise AssertionError(f"Generated Float32 fixture {value!r}: {response}") from exc
            if value is not None:
                assert np.float32(restored["value"]).view(np.uint32) == np.float32(value).view(np.uint32)
            assert c.records_hash(c.VALUE, [restored]) == c.records_hash(c.VALUE, [row])
            projected = client.execute(value_insert_projection([row]) + " FORMAT JSONEachRow")
            restored_projection = c.restore_record(c.VALUE, json.loads(projected.strip()))
            assert c.records_hash(c.VALUE, [restored_projection]) == c.records_hash(c.VALUE, [row])
