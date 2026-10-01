"""Strategy 18 native admission and actual direct/compound cold journal graphs."""
from dataclasses import replace
from datetime import date
import json
import re
import struct
from uuid import UUID

import pytest

from test_arte_rising_momentum_entry_v4 import BitClient
from test_arte_journal_commit_v4 import attached_v4_client
from test_backtest_strategy_initial_momentum import plans
from test_strategy_initial_strong_momentum import witness
from test_strategy_one_intent import _proposal
from src.backend.backtest_strategy_initial_momentum import compile_initial_momentum_plan
from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate, INITIAL_MOMENTUM_REQUIRED
from src.trading_runtime.strategy_initial_strong_momentum import InitialStrongMomentumWitness, InitialMomentumSelectionWitness
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page
from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry
from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry, INITIAL_MOMENTUM, VALUES
from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix


class Client(BitClient):
    def execute(self, sql):
        if "reinterpretAsUInt64" in sql and INITIAL_MOMENTUM.name in sql:
            columns = ",".join(name for name, _ in INITIAL_MOMENTUM.columns)
            raw = super().execute(re.sub(r"SELECT .*? FROM arte\.", f"SELECT {columns} FROM arte.", sql, count=1))
            rows = [json.loads(line) for line in raw.splitlines() if line]
            for row in rows:
                for name in VALUES:
                    value = row[name]
                    row[name + "_bits"] = None if value is None else int.from_bytes(struct.pack(">d", value), "big")
                    row[name] = None if value is None else round(value, 1)
            return "\n".join(json.dumps(row) for row in rows)
        return super().execute(sql)


def unit(sequence=1, prior=0, batch_id=11, boundary=40_100):
    current = witness(boundary)
    selection = InitialMomentumSelectionWitness(InitialStrongMomentumWitness(30_000, witness(30_100)),
                                                 "c" * 64, "d" * 64, "e" * 64)
    proposal = replace(_proposal(), strategy_number=18, boundary_ms=boundary,
                       episode_start_ms=30_000, momentum=current, initial_momentum=selection)
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    batch = strategy_intent_batch(intent, run_id="initial-momentum-run", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=str(UUID(int=51)), batch_id=str(UUID(int=batch_id)),
        prior_batch_id=str(UUID(int=prior)), sequence=sequence, source_cursor="cursor",
        run_status="running", recorded_at=intent.event_time)
    kwargs = dict(run_id=batch.run_id, batch_id=batch.batch_id, parent_record_id=batch.intents[0]["record_id"])
    evidence = project_strategy_one_entry_evidence(proposal, intent, session_date=session, **kwargs)
    kwargs["event_month"] = "2026-08-01"
    momentum = project_rising_momentum_entry(proposal, **kwargs)
    initial = project_initial_momentum_entry(proposal, selection, **kwargs)
    return proposal, V4StrategyOneEntryBatch(batch, (evidence,), momentum_evidence=momentum,
                                           initial_momentum_evidence=initial)


def test_native_first_weak_gate_rejects_later_current_strong():
    candidates, entry, momentum, _ = plans(weak_first=True)
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    gate = compile_static_entry_gate(candidates, entry, strategy_number=18,
                                    momentum_plan=momentum, initial_momentum_plan=initial)
    assert gate.eligible_indices.tolist() == []
    assert int(gate.rejection_mask[1]) == INITIAL_MOMENTUM_REQUIRED
    parent = compile_static_entry_gate(candidates, entry, strategy_number=17, momentum_plan=momentum)
    assert parent.eligible_indices.tolist() == [1]


def test_direct_commit_and_cold_restore_retain_current_anchor_and_plan_receipts():
    proposal, batch = unit()
    client = attached_v4_client(Client())
    publish_strategy_one_entry_batch_v4(client, batch.base, entry_evidence=batch.entry_evidence,
        momentum_evidence=batch.momentum_evidence, initial_momentum_evidence=batch.initial_momentum_evidence)
    prefix = load_verified_v4_prefix(client, batch.base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert page.entries[0].proposal == proposal
    assert INITIAL_MOMENTUM.name in client.inserts
    from src.trading_runtime.arte_strategy_one_entry_journal import load_committed_strategy_one_source
    load_committed_strategy_one_source(client, prefix, page.entries[0])
    forged = replace(proposal, initial_momentum=replace(proposal.initial_momentum, selection_token="f" * 64))
    with pytest.raises(RuntimeError, match="committed first-setup"):
        load_committed_strategy_one_source(client, prefix, replace(page.entries[0], proposal=forged))


def test_compound_commit_rekeys_both_anchor_pairs_and_cold_recovers_sources():
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
    first, batch1 = unit()
    second, batch2 = unit(sequence=2, prior=11, batch_id=12, boundary=40_200)
    client = attached_v4_client(Client())
    compound = coalesce_v4_units((batch1, batch2))
    publish_compound_v4(client, compound)
    prefix = load_verified_v4_prefix(client, batch1.base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert [entry.proposal for entry in page.entries] == [first, second]
    assert all(row["batch_id"] == compound.base.batch_id
               for row in compound.children["initial_momentum_evidence"])


@pytest.mark.parametrize("relative,old,new", [
    ("trading_runtime/strategy_initial_strong_momentum.py",
     "eligible = base_eligible & current_strong & initial_strong", "eligible = base_eligible & current_strong"),
    ("backend/backtest_strategy_initial_momentum.py",
     "strategy_number=12", "strategy_number=1"),
    ("trading_runtime/arte_initial_momentum_entry_v4.py",
     "len(sealed) != 2 * len(required)", "len(sealed) != 0"),
])
def test_source_certificate_rejects_initiality_or_companion_authority_mutations(tmp_path, relative, old, new):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    source = (Path(__file__).parents[1] / "src" / relative).read_text()
    assert old in source
    changed = tmp_path / "mutated.py"
    changed.write_text(source.replace(old, new, 1))
    with pytest.raises(ValueError, match="reviewed source authority changed"):
        certify_rising_momentum_entry_source(source_overrides={relative: changed})


def test_missing_anchor_fails_before_any_typed_insert():
    _, batch = unit()
    client = attached_v4_client(Client())
    with pytest.raises(ValueError, match="missing"):
        publish_strategy_one_entry_batch_v4(client, batch.base, entry_evidence=batch.entry_evidence,
                                            momentum_evidence=batch.momentum_evidence)
    assert client.inserts == []


def test_guarded_intent_rejects_missing_weak_and_foreign_episode_anchor():
    proposal, _ = unit()
    with pytest.raises(ValueError, match="selection"):
        strategy_one_entry_intent(replace(proposal, initial_momentum=None), session_date=date(2026, 8, 18))
    weak = replace(proposal.initial_momentum, initial=replace(proposal.initial_momentum.initial,
                   first_setup=witness(30_100, strong=False)))
    with pytest.raises(ValueError, match="strong initial"):
        strategy_one_entry_intent(replace(proposal, initial_momentum=weak), session_date=date(2026, 8, 18))
    foreign = replace(proposal.initial_momentum, initial=replace(proposal.initial_momentum.initial,
                      episode_start_ms=30_100))
    with pytest.raises(ValueError, match="selection"):
        strategy_one_entry_intent(replace(proposal, initial_momentum=foreign), session_date=date(2026, 8, 18))
