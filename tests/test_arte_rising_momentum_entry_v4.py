"""Strategy 13 momentum is durable scalar authority at every entry boundary."""
import asyncio
import json
import re
import struct
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID
import numpy as np
import pytest
from src.trading_runtime.strategy_rising_momentum_witness import (
    CompletedMomentumObservation, RisingMomentumWitness, rising_momentum_entry,
)
from src.trading_runtime.arte_rising_momentum_entry_v4 import (
    MOMENTUM, VALUES, project_rising_momentum_entry, restore_rising_momentum,
    seal_rising_momentum_rows, decode_momentum_row,
)
from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch, _sealed_families, typed_row
from src.trading_runtime.arte_journal_commit_v4 import (
    publish_strategy_one_entry_batch_v4, publish_base_typed_batch_v4,
    load_verified_v4_prefix, load_verified_commit_v4,
)
from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, publish_compound_v4,
)
from src.trading_runtime.arte_strategy_one_entry_journal import (
    project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page,
    load_committed_strategy_one_source,
)
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from tests.test_strategy_one_intent import _proposal
from tests.test_arte_journal_writer import MemoryClient
from tests.test_arte_journal_commit_v4 import attached_v4_client


def witness(boundary=31_000):
    line = float(np.nextafter(.01, np.inf))
    return RisingMomentumWitness("AAA", boundary, "b" * 64, str(UUID(int=62)), "a" * 64,
        (CompletedMomentumObservation(1000, boundary // 1000 * 1000,
             boundary // 1000 * 1000 - 1000, line, .0, .01, .0),
         CompletedMomentumObservation(10000, boundary // 10000 * 10000,
             boundary // 10000 * 10000 - 10000, None, None, None, None)))


class BitClient(MemoryClient):
    """Model ClickHouse's nullable IEEE projection without decimal JSON fidelity."""
    def execute(self, sql):
        if f"FROM arte.{MOMENTUM.name} " in sql and "reinterpretAsUInt64" in sql:
            raw = super().execute(re.sub(r"SELECT .*? FROM arte\.",
                "SELECT " + ",".join(name for name, _ in MOMENTUM.columns) + " FROM arte.", sql, count=1))
            rows = [json.loads(line) for line in raw.splitlines() if line]
            for row in rows:
                for name in VALUES:
                    value = row[name]
                    row[name + "_bits"] = None if value is None else int.from_bytes(struct.pack(">d", value), "big")
                    # Deliberately round JSON scalar; authoritative bits recover it.
                    row[name] = None if value is None else round(value, 2)
            return "\n".join(json.dumps(row) for row in rows)
        return super().execute(sql)


def unit(sequence=1, prior=0, batch_id=11, boundary=31_000):
    proposal = replace(_proposal(), boundary_ms=boundary, strategy_number=13, momentum=witness(boundary))
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    batch = strategy_intent_batch(intent, run_id="momentum-run", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=str(UUID(int=51)), batch_id=str(UUID(int=batch_id)),
        prior_batch_id=str(UUID(int=prior)), sequence=sequence, source_cursor="cursor",
        run_status="running", recorded_at=intent.event_time)
    evidence = project_strategy_one_entry_evidence(proposal, intent, session_date=session,
        run_id=batch.run_id, batch_id=batch.batch_id, parent_record_id=batch.intents[0]["record_id"])
    momentum = project_rising_momentum_entry(proposal, run_id=batch.run_id,
        batch_id=batch.batch_id, parent_record_id=batch.intents[0]["record_id"], event_month="2026-08-01")
    return proposal, V4StrategyOneEntryBatch(batch, (evidence,), momentum_evidence=momentum)


def test_scalar_witness_reuses_native_exact_float_and_missing_semantics():
    assert rising_momentum_entry(witness())
    value = witness()
    flat = replace(value.observations[0], current_line=.01)
    assert not rising_momentum_entry(replace(value, observations=(flat, value.observations[1])))
    assert not rising_momentum_entry(replace(value, observations=tuple(
        replace(o, current_boundary_ms=0, prior_boundary_ms=0,
                current_line=None, current_signal=None, prior_line=None, prior_signal=None)
        for o in value.observations)))


@pytest.mark.parametrize("updates", [
    {"source_build_id": str(UUID(int=0))}, {"source_attempt_id": "bad"},
    {"market_plan_token": "bad"}, {"boundary_ms": True}, {"ticker": "aaa"},
    {"observations": ()},
])
def test_scalar_rejects_forged_typed_authority(updates):
    with pytest.raises(ValueError):
        rising_momentum_entry(replace(witness(), **updates))


@pytest.mark.parametrize("observation", [
    {"resolution_ms": 10_000}, {"current_boundary_ms": 30_000},
    {"prior_boundary_ms": 29_000}, {"current_line": float("nan")},
    {"current_signal": 0}, {"current_boundary_ms": 0},
])
def test_scalar_rejects_forming_stale_nonadjacent_or_untyped_values(observation):
    value = witness()
    with pytest.raises(ValueError):
        rising_momentum_entry(replace(value, observations=(replace(value.observations[0], **observation), value.observations[1])))


def test_projector_schema_exact_two_rows_and_original_provenance():
    proposal, envelope = unit()
    rows = envelope.momentum_evidence
    assert len(rows) == 2 and [r["resolution_ms"] for r in rows] == [1000, 10000]
    assert "live_market_ssd" in MOMENTUM.ddl()
    assert all(dict(r)["source_attempt_id"] == proposal.momentum.source_attempt_id for r in rows)
    assert all("Nullable(Float64)" == dict(MOMENTUM.columns)[name] for name in VALUES)
    assert restore_rising_momentum(rows, ticker="AAA", boundary_ms=31_000) == proposal.momentum
    intents = dict(_sealed_families(envelope.base))["trading_strategy_intent_v1"]
    assert len(seal_rising_momentum_rows(rows, envelope.entry_evidence, intents, envelope.base.events)) == 2


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "stale", "flat", "parent", "source", "extra"])
def test_direct_seal_rejects_forged_companion_before_insert(mutation):
    _, envelope = unit()
    rows = [dict(r) for r in envelope.momentum_evidence]
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows[1] = rows[0]
    elif mutation == "stale": rows[0]["current_boundary_ms"] = 30_000
    elif mutation == "flat": rows[0]["current_line"] = .01
    elif mutation == "parent": rows[0]["parent_record_id"] = str(UUID(int=999))
    elif mutation == "source": rows[1]["source_attempt_id"] = str(UUID(int=999))
    else: rows.append(rows[0])
    client = attached_v4_client(BitClient())
    with pytest.raises(ValueError):
        publish_strategy_one_entry_batch_v4(client, envelope.base,
            entry_evidence=envelope.entry_evidence, momentum_evidence=tuple(rows))
    assert not client.inserts


def test_direct_generic_entry_child_cannot_bypass_momentum_family():
    _, envelope = unit()
    client = attached_v4_client(BitClient())
    with pytest.raises(ValueError, match="momentum companions"):
        publish_strategy_one_entry_batch_v4(client, envelope.base, entry_evidence=envelope.entry_evidence)
    assert not client.inserts
    with pytest.raises(ValueError):
        publish_base_typed_batch_v4(client, envelope.base)


@pytest.mark.parametrize("compound", [False, True])
def test_direct_and_compound_float64_exact_cold_roundtrip(compound):
    proposal, first = unit()
    client = attached_v4_client(BitClient())
    if compound:
        other, second = unit(2, 11, 12, 31_100)
        combined = coalesce_v4_units((first, second))
        assert len(combined.children["momentum_evidence"]) == 4
        publish_compound_v4(client, combined)
        expected = (proposal, other)
    else:
        publish_strategy_one_entry_batch_v4(client, first.base,
            entry_evidence=first.entry_evidence, momentum_evidence=first.momentum_evidence)
        expected = (proposal,)
    prefix = load_verified_v4_prefix(client, first.base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert tuple(e.proposal for e in page.entries) == expected
    assert page.entries[0].proposal.momentum.observations[0].current_line == float(np.nextafter(.01, np.inf))
    if not compound:
        rebuilt, intent = load_committed_strategy_one_source(client, prefix, page.entries[0])
        assert rebuilt == first.base and intent == page.entries[0].intent


def test_compound_requires_each_entry_own_exact_companions():
    _, first = unit()
    _, second = unit(2, 11, 12, 31_100)
    forged = replace(second, momentum_evidence=())
    client = attached_v4_client(BitClient())
    with pytest.raises(ValueError, match="momentum companions"):
        publish_compound_v4(client, coalesce_v4_units((first, forged)))
    assert not client.inserts


@pytest.mark.parametrize("mutation", ["missing", "flat", "source", "orphan"])
def test_cold_committed_graph_rejects_companion_mutation(mutation):
    _, envelope = unit()
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, envelope.base,
        entry_evidence=envelope.entry_evidence, momentum_evidence=envelope.momentum_evidence)
    rows = client.tables[MOMENTUM.name]
    if mutation == "missing": rows.pop()
    else:
        row = dict(rows[0]); row.pop("content_hash")
        if mutation == "flat": row["current_line"] = .01
        elif mutation == "source": row["source_attempt_id"] = str(UUID(int=999))
        else: row["parent_record_id"] = str(UUID(int=999))
        rows[0] = typed_row(MOMENTUM.name, row)
    with pytest.raises(RuntimeError):
        load_verified_commit_v4(client, run_id=envelope.base.run_id, batch_id=envelope.base.batch_id)


@pytest.mark.parametrize("bad", ["missing", "flat", "ticker", "clock"])
def test_factory_and_runtime_reject_forged_before_portfolio(bad):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    proposal, _ = unit()
    value = proposal.momentum
    if bad == "missing": value = None
    elif bad == "ticker": value = replace(value, ticker="BBB")
    elif bad == "clock": value = replace(value, boundary_ms=31_100)
    else: value = replace(value, observations=(replace(value.observations[0], current_line=.01), value.observations[1]))
    forged = replace(proposal, momentum=value)
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, strategy_id="early-squeeze-strategy",
        strategy_revision=13, account_ids=("DU1",), anchor_date=date(2026, 8, 18))
    runtime.run_id = "momentum-run"
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.portfolio = SimpleNamespace(approve=AsyncMock())
    runtime._execute_intents = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(runtime.submit_strategy_one_proposal(forged))
    runtime.portfolio.approve.assert_not_awaited()
    runtime._execute_intents.assert_not_awaited()


def test_old_entry_rejects_unexpected_witness():
    with pytest.raises(ValueError, match="cannot carry"):
        strategy_one_entry_intent(replace(_proposal(), strategy_number=12, momentum=witness()), session_date=date(2026, 8, 18))



def test_real_hot_journal_projector_and_writer_queue_preserve_companions(monkeypatch):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    from src.trading_runtime import arte_journal_writer as writer_module
    proposal, _ = unit()
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    journal = BacktestMemoryJournal(run_id="momentum-hot")
    record = journal.append_strategy_one_intent(intent=intent, proposal=proposal, session_date=session,
        account_id=proposal.account_id, strategy_id="early-squeeze-strategy", strategy_revision=13)
    projected = project_pending_backtest_v4_prefix(journal, attempt_id=str(UUID(int=91)),
        run_month=date(2026, 8, 1), prior_sequence=0, through_sequence=record.sequence,
        expected_config={"strategy_id": "early-squeeze-strategy", "strategy_revision": 13})
    assert len(projected) == 1 and type(projected[0]) is V4StrategyOneEntryBatch
    assert len(projected[0].momentum_evidence) == 2
    client = attached_v4_client(BitClient())
    monkeypatch.setattr(writer_module, "storage_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda *a: {"mode": "backtest", "account_ids": ("DU1",)})
    writer = ArteJournalWriter(client, run_id=journal.run_id, journal_profile="backtest_v4", coalesce_batches=False)
    try:
        assert writer.submit_strategy_one_entry_v4(projected[0]).result(timeout=5) == projected[0].base.batch_id
        prefix = load_verified_v4_prefix(client, journal.run_id)
        page = load_committed_strategy_one_entry_page(client, prefix)
        assert page.entries[0].proposal == proposal
        assert client.inserts.index(MOMENTUM.name) < client.inserts.index("trading_commit_v4")
    finally:
        writer.close()
        journal.close()


@pytest.mark.parametrize("mutation", ["missing", "flat", "source", "orphan"])
def test_cold_entry_page_reruns_companion_rule_even_with_resealed_rows(mutation):
    proposal, envelope = unit()
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, envelope.base,
        entry_evidence=envelope.entry_evidence, momentum_evidence=envelope.momentum_evidence)
    prefix = load_verified_v4_prefix(client, envelope.base.run_id)
    rows = client.tables[MOMENTUM.name]
    if mutation == "missing": rows.pop()
    else:
        row = dict(rows[0]); row.pop("content_hash")
        if mutation == "flat": row["current_line"] = .01
        elif mutation == "source": row["source_attempt_id"] = str(UUID(int=999))
        else: row["parent_record_id"] = str(UUID(int=999))
        rows[0] = typed_row(MOMENTUM.name, row)
    with pytest.raises(ValueError):
        load_committed_strategy_one_entry_page(client, prefix)


def test_raw_momentum_companions_cannot_be_attached_to_old_entry():
    _, envelope = unit()
    old = ({**dict(envelope.entry_evidence[0]), "strategy_number": 12},)
    with pytest.raises(ValueError, match="missing or extra"):
        seal_rising_momentum_rows(envelope.momentum_evidence, old,
                                 dict(_sealed_families(envelope.base))["trading_strategy_intent_v1"], envelope.base.events)


def test_bit_decode_rejects_null_mismatch():
    _, envelope = unit()
    row = dict(envelope.momentum_evidence[0])
    row.update({name + "_bits": None for name in VALUES})
    with pytest.raises(ValueError, match="null scalar"):
        decode_momentum_row(row)



def test_direct_raw_entry_cannot_forge_number_of_original_parent():
    proposal, envelope = unit()
    from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4
    old_intent = strategy_one_entry_intent(replace(proposal, strategy_number=12, momentum=None),
                                           session_date=date(2026, 8, 18))
    old_base = strategy_intent_batch(old_intent, run_id=envelope.base.run_id,
        run_month=envelope.base.run_month, account_id="DU1", attempt_id=envelope.base.attempt_id,
        batch_id=envelope.base.batch_id, prior_batch_id=envelope.base.prior_batch_id,
        sequence=1, source_cursor="cursor", run_status="running", recorded_at=old_intent.event_time,
        record_id=envelope.base.events[0]["record_id"])
    client = attached_v4_client(BitClient())
    with pytest.raises(ValueError, match="numbered entry intent"):
        _publish_typed_batch_v4(client, old_base, strategy_one_entry_rows=envelope.entry_evidence,
                               rising_momentum_rows=envelope.momentum_evidence)
    assert not client.inserts


def test_source_recovery_refuses_replaced_rising_witness():
    proposal, envelope = unit()
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, envelope.base,
        entry_evidence=envelope.entry_evidence, momentum_evidence=envelope.momentum_evidence)
    prefix = load_verified_v4_prefix(client, envelope.base.run_id)
    entry = load_committed_strategy_one_entry_page(client, prefix).entries[0]
    changed = replace(proposal.momentum, market_plan_token="c" * 64)
    with pytest.raises(RuntimeError, match="committed momentum detail"):
        load_committed_strategy_one_source(client, prefix, replace(entry,
            proposal=replace(proposal, momentum=changed)))
