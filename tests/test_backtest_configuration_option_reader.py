import json
from hashlib import sha256
from unittest.mock import patch

import pytest

from src.backend.backtest_configuration_option_reader import (
    ConfigurationOptionReader, certified_configuration_options,
)
from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_contract import STRATEGY_ID


class Source:
    def __init__(self):
        self.queries = []
        self.closed = False
        self.payload = {"strategy": {"strategy_id": STRATEGY_ID, "strategy_number": 1,
            "revision": 1, "execution_interval": "100ms", "parameters": {
                "execution": {"tick_size": 0.01}}}, "accounts": {"bindings": []},
            "run_plan": {"run_plan_id": "balanced-replay"}}
        self.nodes = list(encode_nodes(self.payload))
        self.release = {"release_attempt_id": "00000000-0000-0000-0000-000000000001",
            "strategy_id": STRATEGY_ID, "source_candidate_id": "candidate-350",
            "source_candidate_hash": "a" * 64,
            "payload_hash": sha256(canonical_json(self.payload).encode()).hexdigest(),
            "node_count": len(self.nodes), "node_hash": node_hash(self.nodes)}

    def execute(self, query):
        self.queries.append(query)
        if "SELECT DISTINCT" in query:
            return json.dumps({"strategy_number": 1})
        return "\n".join(json.dumps(r) for r in (
            [self.release] if "configuration_release" in query else self.nodes))

    def close(self):
        self.closed = True


def test_repeated_certification_reuses_reads_but_preserves_actual_seals_and_final_fence():
    source = Source()
    reader = ConfigurationOptionReader(source)
    first = certify_numbered_configuration(reader)
    assert certify_numbered_configuration(reader) == first
    assert len(source.queries) == 2
    reader.verify_unchanged()
    assert len(source.queries) == 4
    source.nodes[0] = {**source.nodes[0], "text_value": "changed"}
    with pytest.raises(RuntimeError, match="source changed"):
        reader.verify_unchanged()
    with pytest.raises(RuntimeError, match="nodes differ"):
        certify_numbered_configuration(ConfigurationOptionReader(source))


def test_options_request_closes_reader_and_does_not_reuse_prior_request():
    sources = [Source(), Source()]
    with patch("src.backend.backtest_configuration_option_reader.readonly_clickhouse_client",
               side_effect=sources) as factory:
        first, options = certified_configuration_options()
        second, again = certified_configuration_options()
    assert first == second and options == again
    assert all(s.closed and len(s.queries) == 6 for s in sources)
    assert factory.call_count == 2


def test_malformed_release_is_not_hidden_by_request_reuse():
    source = Source()
    source.release["node_hash"] = "0" * 64
    with patch("src.backend.backtest_configuration_option_reader.readonly_clickhouse_client",
               return_value=source), pytest.raises(RuntimeError, match="nodes differ"):
        certified_configuration_options()
    assert source.closed


def test_unknown_identity_and_query_memory_budgets_fail_closed():
    source = Source()
    with patch("src.backend.backtest_configuration_option_reader.readonly_clickhouse_client",
               return_value=source), pytest.raises(ValueError, match="Unknown immutable"):
        certified_configuration_options("old")
    assert source.closed
    with pytest.raises(ValueError):
        ConfigurationOptionReader(Source()).execute("INSERT INTO x VALUES (1)")
    with pytest.raises(ValueError, match="query budget"):
        ConfigurationOptionReader(Source(), max_queries=0).execute("SELECT 1")
    with pytest.raises(ValueError, match="memory budget"):
        ConfigurationOptionReader(Source(), max_bytes=1).execute("SELECT 1")
