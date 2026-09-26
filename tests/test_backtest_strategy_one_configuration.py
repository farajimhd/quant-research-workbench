from __future__ import annotations

from hashlib import sha256
import json

import pytest

from src.backend.backtest_strategy_one_configuration import (
    certify_strategy_one_configuration, selected_strategy_one_revision,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import (
    encode_nodes, node_hash,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID


PAYLOAD = {"strategy": {"strategy_id": STRATEGY_ID, "strategy_number": 1,
                        "revision": 1, "execution_interval": "100ms",
                        "parameters": {"execution": {"tick_size": 0.01}}},
           "accounts": {"bindings": []}}
ATTEMPT = "00000000-0000-0000-0000-000000000001"


class Reader:
    def __init__(self, *, payload=PAYLOAD, releases=None, rows=None):
        self.rows = list(encode_nodes(payload)) if rows is None else rows
        self.releases = ([{"release_attempt_id": ATTEMPT,
                          "strategy_id": STRATEGY_ID,
                          "source_candidate_id": "candidate-350",
                          "source_candidate_hash": "a" * 64,
                          "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
                          "node_count": len(self.rows), "node_hash": node_hash(self.rows)}]
                         if releases is None else releases)
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        selected = self.releases if "configuration_release" in query else self.rows
        return "\n".join(json.dumps(row) for row in selected)


def test_read_only_release_reconstructs_pinned_numbered_configuration():
    reader = Reader()
    release = certify_strategy_one_configuration(reader)
    assert release.payload == PAYLOAD
    assert release.revision()["revision"] == 1
    assert release.revision()["content_hash"] == release.payload_hash
    assert len(reader.queries) == 2
    assert all(query.startswith("SELECT ") for query in reader.queries)


def test_unreleased_or_duplicate_strategy_number_is_rejected():
    for releases in ([], Reader().releases * 2):
        with pytest.raises(RuntimeError):
            certify_strategy_one_configuration(Reader(releases=releases))


def test_changed_node_or_legacy_strategy_is_rejected():
    changed = [dict(row) for row in encode_nodes(PAYLOAD)]
    next(row for row in changed if row["value_kind"] == "float")["float_value"] = 0.02
    with pytest.raises(RuntimeError):
        certify_strategy_one_configuration(
            Reader(rows=changed, releases=Reader().releases))
    legacy = {"strategy": {**PAYLOAD["strategy"], "strategy_id": "legacy"}}
    with pytest.raises(RuntimeError):
        certify_strategy_one_configuration(Reader(payload=legacy))


def test_selector_accepts_only_exact_release_and_run_plan():
    payload = {**PAYLOAD, "run_plan": {"run_plan_id": "strategy-one-plan"}}
    reader = Reader(payload=payload)
    revision = selected_strategy_one_revision(client=reader,
        run_plan_id="strategy-one-plan")
    assert revision["revision_id"].startswith("strategy-one-1:")
    assert all(query.startswith("SELECT ") for query in reader.queries)
    with pytest.raises(ValueError):
        selected_strategy_one_revision(client=Reader(payload=payload),
                                       revision_id="candidate-350")
    with pytest.raises(ValueError):
        selected_strategy_one_revision(client=Reader(payload=payload),
                                       run_plan_id="legacy-plan")


def test_app_backtest_selector_does_not_read_sqlite_candidate(monkeypatch):
    from src.backend import trading_configuration_service as service
    from src.backend import backtest_strategy_one_configuration as numbered

    monkeypatch.setattr(service, "candidate_runtime_configuration_snapshot",
                        lambda *_args, **_kwargs: pytest.fail("legacy candidate read"))
    monkeypatch.setattr(service, "approved_runtime_configuration_snapshot",
                        lambda *_args, **_kwargs: pytest.fail("legacy release read"))
    monkeypatch.setattr(numbered, "selected_strategy_one_revision",
                        lambda **kwargs: kwargs)
    assert service.backtest_configuration_snapshot(
        "strategy-one-plan", candidate_id="strategy-one-1:attempt") == {
            "run_plan_id": "strategy-one-plan",
            "revision_id": "strategy-one-1:attempt"}
