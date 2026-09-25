"""Inactive fixed journal assembly performs no disk or database writes."""
from datetime import date, datetime, timezone

import pytest

from src.backend import backtest_fixed_journal_bootstrap as bootstrap


RUN = "00000000-0000-0000-0000-000000000a01"
ATTEMPT = "00000000-0000-0000-0000-000000000a02"


def _verified(monkeypatch):
    checked = []
    monkeypatch.setattr(bootstrap, "storage_preflight",
                        lambda *_: checked.append("schema"))
    monkeypatch.setattr(bootstrap, "terminal_v2_operator_preflight",
                        lambda *_: checked.append("v2"))
    monkeypatch.setattr(bootstrap, "terminal_v2_keeper_proof_preflight",
                        lambda *_: checked.append("keeper"))
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "market-token"}
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *_, **__: context)
    return checked, context


def test_bootstrap_assembles_bounded_in_memory_lane_without_writes(monkeypatch):
    checked, _ = _verified(monkeypatch)
    token = bootstrap.prepare_fixed_journal_token(
        object(), object(), run_id=RUN, account_ids=("DU1",),
        configuration_hash="c" * 64, market_plan_token="market-token",
        projection_certifier=lambda: "a" * 64)
    assert checked == ["schema", "v2", "keeper"]
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        coalesce_batches = False
        max_events_per_commit = 512
        def close(self):
            pass
    calls = []
    def factory(*args, **kwargs):
        calls.append(kwargs)
        return Writer()
    assembly = bootstrap.assemble_fixed_journal(
        object(), object(), token, attempt_id=ATTEMPT,
        expected_config={"mode": "backtest"},
        fixed_market_parent_plan=object(), fixed_market_execution_plan=object(),
        expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        writer_factory=factory)
    assert assembly.journal.run_id == RUN
    assert assembly.publisher.writer is assembly.writer
    assert assembly.terminal_authority.account_ids == ("DU1",)
    assert calls == [{"run_id": RUN, "capacity": 8,
                      "max_events_per_commit": 512, "coalesce_batches": False}]
    assembly.journal.close()


def test_bootstrap_rejects_unverified_context_or_missing_projection(monkeypatch):
    _, context = _verified(monkeypatch)
    with pytest.raises(ValueError, match="projection"):
        bootstrap.prepare_fixed_journal_token(
            object(), object(), run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="market-token",
            projection_certifier=None)
    context["market_plan_token"] = "changed"
    with pytest.raises(RuntimeError, match="differs"):
        bootstrap.prepare_fixed_journal_token(
            object(), object(), run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="market-token",
            projection_certifier=lambda: "a" * 64)
