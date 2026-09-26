from __future__ import annotations

from copy import deepcopy

import pytest

from pipelines.strategy_one.configuration_migration import (
    SOURCE_CANDIDATE_HASH, SOURCE_CANDIDATE_ID,
)
from pipelines.strategy_one.configuration_publisher import (
    _verified_envelope, publication_envelope,
)


def _source():
    return {"revision_id": SOURCE_CANDIDATE_ID,
            "content_hash": SOURCE_CANDIDATE_HASH,
            "release_state": "test_candidate",
            "payload": {
                "strategy": {"strategy_id": "long-momentum-campaign",
                             "revision": 47, "execution_interval": "100ms",
                             "parameters": {"execution": {"tick_size": 0.01}}},
                "run_plan": {"activation": {}},
                "strategy_profile": {"lifecycle": {"trading_behavior": {
                    "side": "long", "eligible_sessions": ["premarket"]}}},
                "assignments": [],
            }}


def test_transfer_compiles_typed_numbered_body_without_source_mutation():
    source = _source()
    before = deepcopy(source)
    envelope = publication_envelope(source)
    payload, nodes = _verified_envelope(envelope)
    assert source == before
    assert len(nodes) == envelope["node_count"]
    assert payload["strategy"]["strategy_number"] == 1
    assert payload["strategy_profile"]["lifecycle"] == {
        "trading_behavior": {"side": "long", "eligible_sessions": ["premarket"]}}


@pytest.mark.parametrize("key,value", [
    ("source_candidate_hash", "0" * 64),
    ("node_hash", "0" * 64),
    ("node_count", 1),
])
def test_transfer_rejects_changed_seals(key, value):
    envelope = publication_envelope(_source())
    envelope[key] = value
    with pytest.raises(ValueError):
        _verified_envelope(envelope)


def test_transfer_rejects_changed_strategy_body():
    envelope = publication_envelope(_source())
    envelope["payload"]["strategy"]["revision"] = 2
    with pytest.raises(ValueError):
        _verified_envelope(envelope)
