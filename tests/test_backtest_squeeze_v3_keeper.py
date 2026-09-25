"""Cold V3 prefix must agree with both CH and durable Keeper watermark."""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.backend import backtest_squeeze_v3_keeper as subject
from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable


RUN = "backtest:squeeze"
BATCH = "00000000-0000-0000-0000-000000000a12"


def _setup(monkeypatch, *, sequence=1, commit=None, context_ok=True):
    row = {"batch_id": BATCH, "last_sequence": 1}
    digest = sha256(canonical_json(row).encode()).hexdigest()
    calls = []
    class Barrier:
        prefix_verified = False
        def verify_run_context_receipt(self, client):
            calls.append("context")
            if not context_ok:
                raise KeeperUnavailable("context receipt absent")
        def assert_fenced(self, run_id):
            assert run_id == RUN and self.prefix_verified
            calls.append("fenced")
        def release(self):
            calls.append("release")
    class Dispatch:
        def acquire_cold_barrier(self, run_id):
            assert run_id == RUN
            calls.append("closed")
            return Barrier()
        def _read_gate(self, run_id):
            return SimpleNamespace(compacted_through=sequence,
                                   compacted_batch_id=BATCH,
                                   compacted_commit_hash=digest), 1
    monkeypatch.setattr(subject, "TypedInsertDispatch", Dispatch)
    monkeypatch.setattr(subject, "load_verified_squeeze_v3_prefix",
                        lambda *_, **__: V3CommittedPrefix(
                            RUN, 1, BATCH, "bar:1", "running", (BATCH,), ()))
    monkeypatch.setattr(subject, "_rows", lambda *_, **__: [row] if commit is None else commit)
    return Dispatch(), calls


def test_v3_cold_barrier_verifies_context_whole_prefix_and_exact_commit(monkeypatch):
    dispatch, calls = _setup(monkeypatch)
    prefix = subject.load_keeper_attested_squeeze_v3_prefix(
        object(), dispatch, run_id=RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert prefix.last_batch_id == BATCH
    assert calls == ["closed", "context", "fenced", "fenced", "release"]


@pytest.mark.parametrize("change", ["watermark", "duplicate", "tamper", "receipt"])
def test_v3_cold_barrier_fails_closed_and_releases(monkeypatch, change):
    dispatch, calls = _setup(
        monkeypatch, sequence=2 if change == "watermark" else 1,
        commit=([{"batch_id": BATCH, "last_sequence": 2}]
                if change == "tamper" else
                [{"batch_id": BATCH, "last_sequence": 1}] * 2
                if change == "duplicate" else None),
        context_ok=change != "receipt")
    with pytest.raises(KeeperUnavailable):
        subject.load_keeper_attested_squeeze_v3_prefix(
            object(), dispatch, run_id=RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    assert calls[-1] == "release"
