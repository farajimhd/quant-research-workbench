from __future__ import annotations

from copy import deepcopy

import pytest

from pipelines.strategy_one.configuration_migration import (
    SOURCE_CANDIDATE_HASH, SOURCE_CANDIDATE_ID,
    compile_strategy_one_configuration,
)
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes
from src.trading_runtime.strategy_one_contract import REQUIRED_INPUTS, STRATEGY_ID


def _source():
    return {"revision_id": SOURCE_CANDIDATE_ID,
            "content_hash": SOURCE_CANDIDATE_HASH,
            "release_state": "test_candidate",
            "payload": {
                "strategy": {"strategy_id": "long-momentum-campaign",
                             "revision": 47, "execution_interval": "100ms",
                             "parameters": {"execution": {"tick_size": 0.01}}},
                "run_plan": {"signal_stream_ids": ["price-squeeze-early"],
                             "watchlist_ids": ["legacy-watchlist"],
                             "activation": {"watchlist_policy": "not_required"},
                             "data_plan_ids": {"old": "qmd"}},
                "signal_activation": {"column_catalog": [{"legacy": "value"}]},
                "assignments": [], "accounts": {"bindings": []},
            }}


def test_migration_keeps_source_immutable_and_removes_qmd_catalog():
    source = _source()
    prior = deepcopy(source)
    payload, payload_hash, nodes_hash, count = compile_strategy_one_configuration(source)
    assert source == prior
    assert payload["strategy"]["strategy_id"] == STRATEGY_ID
    assert payload["strategy"]["strategy_number"] == 1
    assert payload["strategy"]["revision"] == 1
    assert "column_catalog" not in payload["signal_activation"]
    assert payload["run_plan"]["watchlist_ids"] == []
    assert payload["run_plan"]["data_plan_ids"] == {}
    assert payload["strategy"]["parameters"] == {
        "execution": {"tick_size": 0.01}, "sizing": {}}
    assert payload["strategy_profile"]["profile_id"] == "strategy-one-1"
    assert payload["strategy_profile"]["parameters"] == {}
    assert "initial_entry" not in payload["strategy_profile"]["lifecycle"]
    assert len(payload["run_plan"]["observation_dependencies"]) == len(REQUIRED_INPUTS)
    assert count == len(encode_nodes(payload))
    assert len(payload_hash) == len(nodes_hash) == 64


@pytest.mark.parametrize("change", [
    lambda source: source.update(content_hash="0" * 64),
    lambda source: source["payload"]["strategy"].update(revision=48),
    lambda source: source["payload"].update(assignments=[{"ticker": "AAA"}]),
    lambda source: source["payload"]["strategy"]["parameters"]["execution"].update(
        tick_size=0),
])
def test_migration_rejects_wrong_or_ambiguous_base(change):
    source = _source()
    change(source)
    with pytest.raises(ValueError):
        compile_strategy_one_configuration(source)
