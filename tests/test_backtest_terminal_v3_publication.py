"""Terminal suffix uses one held cold fence and operation-bound proof."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.backend import backtest_terminal_v3_publication as subject
from src.backend.backtest_terminal_v3_keeper import operation_inventory_hash
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_terminal_v3_fence import _fixture


def test_v3_suffix_keeps_cold_gate_through_terminal_operation_proof(monkeypatch):
    prefix, kwargs = _fixture()
    calls = []
    seal = subject.project_terminal_v3_commit(prefix, **kwargs)
    operation = (("/operation", 1, "a" * 64),)

    class Authority:
        account_ids = ("DU1",)
        keeper = object()
        _leases = ({"owner_id": "owner", "epoch": 1},)
        def __init__(self):
            self.client = SimpleNamespace(_client=object())
        def assert_current(self, *_):
            calls.append("claim")

    @contextmanager
    def barrier(*_, **__):
        calls.append("close")
        yield SimpleNamespace(prefix=prefix, barrier=object())
        calls.append("release")

    class Client:
        def __init__(self, *_, **__):
            calls.append("dispatch")
        def execute(self, sql):
            assert sql.startswith("INSERT INTO arte.trading_backtest_terminal_commit_v3")
            calls.append("seal_insert")
        def acknowledged_operations(self):
            calls.append("operations")
            return operation
        def assert_covers(self, tables):
            calls.append("coverage")
        def assert_account_covers(self, tables):
            calls.append("account_coverage")

    class Capture:
        selected_policy = None

    monkeypatch.setattr(subject, "FixedTerminalKeeperAuthority", Authority)
    monkeypatch.setattr(subject, "TypedInsertDispatch", object)
    monkeypatch.setattr(subject, "CapturedPortfolioSnapshot", Capture)
    monkeypatch.setattr(subject, "attested_squeeze_v3_barrier", barrier)
    monkeypatch.setattr(subject, "TerminalV3DispatchClient", Client)
    monkeypatch.setattr(subject, "storage_preflight", lambda *_, **__: None)
    monkeypatch.setattr(subject, "_insert_missing",
                        lambda _client, table, **__: calls.append(table))
    monkeypatch.setattr(subject, "_publish_portfolio_anchors",
                        lambda *_, **__: calls.append("anchors"))
    monkeypatch.setattr(subject, "_rows", lambda *_, **__: [])
    monkeypatch.setattr(subject, "_stored_portfolio_rows", lambda *_, **__: ())
    monkeypatch.setattr(subject, "load_terminal_v3_commit",
                        lambda *_, **__: calls.append("cold_seal") or seal)
    monkeypatch.setattr(subject, "_account_states",
                        lambda *_, **__: {"DU1": {"state_hash": "b" * 64}})
    monkeypatch.setattr(subject, "attest_terminal_v3",
                        lambda *_, **__: calls.append("proof"))
    result = subject.publish_terminal_v3_suffix(
        object(), Authority(), object(), run_id=prefix.run_id,
        account_ids=("DU1",), expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64,
        portfolio_captures=(Capture(),),
        **{key: value for key, value in kwargs.items() if key != "account_ids"})
    assert result == seal
    assert calls.index("close") < calls.index("anchors") < calls.index("seal_insert")
    assert calls.index("seal_insert") < calls.index("operations") < calls.index("proof")
    assert calls.index("proof") < calls.index("release")


def test_v3_cold_state_rejects_changed_operation_inventory(monkeypatch):
    prefix, kwargs = _fixture()
    seal = subject.project_terminal_v3_commit(prefix, **kwargs)
    states = {"DU1": {"state_hash": "b" * 64}}
    operations = (("/changed", 2, "d" * 64),)
    expected_operation_hash = operation_inventory_hash(operations)
    proof = "\n".join((
        "4", prefix.run_id, str(seal["batch_id"]),
        sha256(canonical_json(seal).encode()).hexdigest(),
        sha256(canonical_json([("DU1", "b" * 64)]).encode()).hexdigest(),
        expected_operation_hash, "1", "DU1", "owner", "1",
    )).encode()

    @contextmanager
    def barrier(*_, **__):
        yield SimpleNamespace(prefix=prefix, barrier=object())

    class Client:
        def __init__(self, *_, **__):
            pass
        def acknowledged_operations(self):
            return operations
        def assert_covers(self, tables):
            pass
        def assert_account_covers(self, tables):
            pass

    monkeypatch.setattr(subject, "storage_preflight", lambda *_, **__: None)
    monkeypatch.setattr(subject, "attested_squeeze_v3_barrier", barrier)
    monkeypatch.setattr(subject, "TerminalV3DispatchClient", Client)
    monkeypatch.setattr(subject, "load_terminal_v3_commit", lambda *_, **__: seal)
    monkeypatch.setattr(subject, "_account_states", lambda *_, **__: states)
    monkeypatch.setattr(subject, "_stored_portfolio_rows", lambda *_, **__: ())
    monkeypatch.setattr(subject, "load_terminal_v3_receipt", lambda *_, **__: proof)
    assert subject.load_attested_terminal_v3_state(
        object(), object(), object(), run_id=prefix.run_id,
        account_ids=("DU1",), expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) == (seal, states)
    corrupted = proof.decode().split("\n")
    corrupted[5] = "c" * 64
    monkeypatch.setattr(subject, "load_terminal_v3_receipt",
                        lambda *_, **__: "\n".join(corrupted).encode())
    with pytest.raises(RuntimeError, match="differs"):
        subject.load_attested_terminal_v3_state(
            object(), object(), object(), run_id=prefix.run_id,
            account_ids=("DU1",), expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
