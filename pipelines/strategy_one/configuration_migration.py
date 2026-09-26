"""One-time Candidate 350 to immutable Strategy 1 configuration compiler.

Only this migration reads the old candidate; neither Backtest nor live
Strategy 1 imports its SQLite store. The source candidate is never changed.
The large legacy QMD column catalog is intentionally not copied: the numbered
executor declares and certifies its own persisted ARTE inputs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping

from src.backend.fixed_bar_signal import canonical_stream_activation
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import (
    encode_nodes, node_hash,
)
from src.trading_runtime.strategy_one_contract import (
    EVALUATION_INTERVAL, REQUIRED_INPUTS, STRATEGY_ID, STRATEGY_NUMBER,
)


SOURCE_CANDIDATE_ID = "cd65b0cc-78b7-4f68-81c1-3ada362c9fd1"
SOURCE_CANDIDATE_HASH = "774b4e53fc7e984dd17b846c9ad906cd1f81f45a2104eb303b545209297ba36a"
SOURCE_STRATEGY_ID = "long-momentum-campaign"
SOURCE_STRATEGY_REVISION = 47


def compile_strategy_one_configuration(
    source_revision: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, int]:
    """Return a bounded typed release candidate, not a published release."""
    if (source_revision.get("revision_id") != SOURCE_CANDIDATE_ID
            or source_revision.get("content_hash") != SOURCE_CANDIDATE_HASH
            or source_revision.get("release_state") != "test_candidate"):
        raise ValueError("Strategy 1 migration source differs from Candidate 350")
    source = dict(source_revision.get("payload") or {})
    strategy = dict(source.get("strategy") or {})
    if (strategy.get("strategy_id") != SOURCE_STRATEGY_ID
            or strategy.get("revision") != SOURCE_STRATEGY_REVISION
            or strategy.get("execution_interval") != EVALUATION_INTERVAL
            or source.get("assignments")):
        raise ValueError("Candidate 350 runtime scope differs from Strategy 1 base")
    payload = deepcopy(source)
    numbered = payload["strategy"]
    numbered.update(strategy_id=STRATEGY_ID, strategy_number=STRATEGY_NUMBER,
                    revision=STRATEGY_NUMBER, name="Early Squeeze Strategy 1",
                    execution_interval=EVALUATION_INTERVAL)
    tick = dict(dict(numbered.get("parameters") or {}).get("execution") or {}).get(
        "tick_size")
    if type(tick) not in (int, float) or not isfinite(tick) or tick <= 0:
        raise ValueError("Candidate 350 lacks a valid pinned execution tick")
    # Strategy 1's rules are the numbered, source-reviewed rules in
    # strategy_one_contract.  Candidate 350's profile and strategy parameters
    # describe forming MACD and an ordinal-support stop; retaining those as a
    # published Strategy 1 configuration would create a second, false rule
    # authority even though the fixed executor ignores them.
    sizing = deepcopy(dict(numbered.get("parameters") or {}).get("sizing") or {})
    numbered["profile_id"] = "strategy-one-1"
    numbered["profile_revision"] = STRATEGY_NUMBER
    numbered["parameters"] = {
        "execution": {"tick_size": tick},
        "sizing": sizing,
    }
    numbered["action_definitions"] = []
    numbered["action_policies"] = []
    old_profile = dict(payload.get("strategy_profile") or {})
    old_behavior = dict(dict(old_profile.get("lifecycle") or {}).get(
        "trading_behavior") or {})
    payload["strategy_profile"] = {
        "profile_id": "strategy-one-1",
        "revision": STRATEGY_NUMBER,
        "definition_id": STRATEGY_ID,
        "definition_revision": STRATEGY_NUMBER,
        "name": "Early Squeeze Strategy 1",
        "description": (
            "Numbered causal 100ms Strategy 1. Completed MACD and last completed "
            "30s low; protection and target are defined by strategy_one_contract."
        ),
        "execution_interval": EVALUATION_INTERVAL,
        "parameters": {},
        "lifecycle": {"trading_behavior": old_behavior},
        "action_policy_ids": [],
        "enabled": True,
    }
    stream, activation = canonical_stream_activation()
    stream["occurrence_source"] = "arte.strategy_one_candidate_v1"
    payload["signal_activation"] = {
        "signal_streams": [stream], "rule_sets": activation["rule_sets"]}
    run_plan = payload["run_plan"]
    run_plan["name"] = "Strategy 1 Backtest"
    run_plan["description"] = "Certified ARTE 100ms causal Strategy 1"
    run_plan["profile_id"] = "strategy-one-1"
    run_plan["signal_stream_ids"] = [stream["signal_stream_id"]]
    run_plan["watchlist_ids"] = []
    run_plan.setdefault("activation", {})["watchlist_policy"] = "not_required"
    run_plan["observation_dependencies"] = [
        {"input": item, "producer": "arte", "required": True}
        for item in REQUIRED_INPUTS]
    # Old QMD data-plan IDs are not a runtime input of the numbered strategy.
    run_plan["data_plan_ids"] = {}
    nodes = encode_nodes(payload)
    if len(nodes) > 5_000:
        raise RuntimeError("Strategy 1 configuration retained a legacy catalog")
    return (payload,
            sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
            node_hash(nodes), len(nodes))
