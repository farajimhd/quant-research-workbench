from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.strategy_one_configuration_tree import (
    MAX_NODES, decode_nodes, ddl, encode_nodes, node_hash,
)


CONFIG = {
    "strategy": {"strategy_id": "early-squeeze-strategy", "revision": 1,
                 "parameters": {"execution": {"tick_size": 0.01},
                                "flags": [True, False, None]}},
    "accounts": {"bindings": [{"account_key": "replay", "enabled": True}]},
    "empty_object": {}, "empty_array": [],
}


def test_typed_nodes_round_trip_and_seal_without_json_column():
    nodes = encode_nodes(CONFIG)
    assert decode_nodes(nodes) == CONFIG
    assert node_hash(nodes) == node_hash(encode_nodes(deepcopy(CONFIG)))
    assert len(node_hash(nodes)) == 64
    statements = ddl()
    assert all("storage_policy='live_market_ssd'" in sql for sql in statements)
    assert all("JSON" not in sql and "Blob" not in sql for sql in statements)
    assert "parent_node_id Nullable(UInt32)" in statements[0]
    assert "float_value Nullable(Float64)" in statements[0]


def test_dictionary_order_does_not_change_node_identity():
    reordered = dict(reversed(list(CONFIG.items())))
    assert encode_nodes(reordered) == encode_nodes(CONFIG)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1 << 63,
                                  "x" * 1025, object()])
def test_unsupported_or_blob_like_scalar_is_rejected(value):
    with pytest.raises(ValueError):
        encode_nodes({"bad": value})


@pytest.mark.parametrize("change", [
    lambda rows: rows[1].update(parent_node_id=99),
    lambda rows: rows[1].update(node_id=7),
    lambda rows: rows[1].update(text_value="unexpected"),
    lambda rows: rows[1].update(child_key=None),
])
def test_corrupt_topology_or_scalar_is_rejected(change):
    rows = [dict(row) for row in encode_nodes(CONFIG)]
    change(rows)
    with pytest.raises(ValueError):
        decode_nodes(rows)


def test_node_count_is_bounded():
    with pytest.raises(ValueError):
        encode_nodes({"items": list(range(MAX_NODES))})
