"""Qualify the Rust-built QMD SELECT on generated ClickHouse rows only."""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re

import numpy as np
import pytest

from pipelines.market_sip.events.columnar_history_producer import value_insert_projection
from src.market_engine import columnar_history_product as c
from test_columnar_history_publication import source, CODE


@pytest.mark.skipif(os.environ.get("QUALIFY_HISTORY_SQL_PROBE") != "1",
                    reason="Explicit read-only Rust/ClickHouse SQL qualification")
def test_compiled_rust_window_query_preserves_bits_validity_and_interval_selection():
    from src.backend.backtest_v3_clients import v3_client
    root = Path(__file__).resolve().parents[1]
    artifact = Path("D:/TradingML/runtimes/task-0223-app/qmd-columnar-history-select-probe.json")
    probe = json.loads(artifact.read_text(encoding="utf-8"))
    assert probe["source_sha256"] == sha256((root / "services/qmd_history_gateway/src/columnar_history.rs").read_bytes()).hexdigest()
    query = next(q for q in probe["queries"] if "FROM arte.qmd_causal_history_value_v1" in q)
    scope = source().scope("00000000-0000-0000-0000-000000000002", CODE)
    values = [None, float(np.float32(-0.)), float(np.finfo(np.float32).tiny)]
    rows = [{**scope.prefix(), "field_id": 326, "first_ordinal": i, "stop_ordinal": i + 1,
        "value": value, "value_bits": None if value is None else int(np.float32(value).view(np.uint32)),
        "valid": int(value is not None)} for i, value in enumerate(values)]
    # Replace only the absent production table/scope with generated scalar
    # fixtures. Preserve the actual compiled SELECT, bit guard and window gate.
    query = query.replace("FROM arte.qmd_causal_history_value_v1", "FROM (" + value_insert_projection(rows) + ")")
    query = re.sub(r"WHERE source_build_id=.*? AND field_id IN", "WHERE 1 AND field_id IN", query, flags=re.S)
    assert "arte." not in query
    with closing(v3_client("read")) as client:
        result = [json.loads(line) for line in client.execute(query).splitlines() if line.strip()]
    assert len(result) == 2  # Compiled window ends at ordinal 2, excluding row 2.
    assert [r["first_ordinal"] for r in result] == [0, 1]
    assert [r["valid"] for r in result] == [0, 1]
    assert [r["bits_consistent"] for r in result] == [1, 1]
    assert result[0]["value_bits"] is None
    assert result[1]["value_bits"] == 0x80000000
