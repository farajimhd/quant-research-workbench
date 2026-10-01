"""Strategy 17's actual source gate, scalar admission and cold typed restore."""
import pyarrow as pa
import pytest

from test_backtest_strategy_rising_momentum import ArrowSource, sources, _facts, _entry
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate, RISING_MOMENTUM_REQUIRED
from src.backend.backtest_strategy_one_stateful import propose_certified_strategy_one_entry


class GrowthSource(ArrowSource):
    def __init__(self, histogram):
        super().__init__()
        self.histogram = histogram

    def iter_arrow_record_batches(self, sql):
        for batch in super().iter_arrow_record_batches(sql):
            rows = batch.to_pylist()
            for row in rows:
                if row['resolution_ms'] == 10_000:
                    row['macd_line'] = self.histogram if row['boundary_ms'] == 30_000 else 1.0
            yield pa.RecordBatch.from_pylist(rows, schema=batch.schema)


@pytest.mark.parametrize('histogram,accepted', [(1.05, False), (1.2, True)])
def test_certified_source_native_gate_and_scalar_admission_agree(histogram, accepted):
    plans = sources()
    reader = GrowthSource(histogram)
    momentum = load_rising_momentum_plan(*plans[:2], client=reader)
    assert momentum.eligible_mask(14).tolist() == [True]
    assert momentum.eligible_mask(17).tolist() == [accepted]
    gate = compile_static_entry_gate(plans[1], _entry(plans), strategy_number=17,
                                     momentum_plan=momentum)
    assert gate.rejection_mask.tolist() == [0 if accepted else RISING_MOMENTUM_REQUIRED]
    witness = momentum.lookup('AAA', 31_000)
    decision = propose_certified_strategy_one_entry(*_facts(), strategy_number=17, momentum=witness)
    assert (decision.proposal is not None) is accepted
    assert len(reader.queries) == 1


def test_direct_typed_commit_cold_restore_retains_seventeen_identity_and_witness(monkeypatch):
    from test_arte_rising_momentum_entry_v4 import unit, BitClient
    from test_arte_journal_commit_v4 import attached_v4_client
    from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix
    from src.trading_runtime.arte_strategy_one_entry_journal import load_committed_strategy_one_entry_page
    from dataclasses import replace
    import test_arte_rising_momentum_entry_v4 as fixture
    original = fixture.witness
    def strong_witness(boundary=31_000):
        value = original(boundary)
        ten = replace(value.observations[1], current_line=0.12, current_signal=0.0,
                      prior_line=0.10, prior_signal=0.0)
        return replace(value, observations=(value.observations[0], ten))
    monkeypatch.setattr(fixture, 'witness', strong_witness)
    proposal, batch = unit(strategy_number=17)
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, batch.base, entry_evidence=batch.entry_evidence,
                                      momentum_evidence=batch.momentum_evidence)
    prefix = load_verified_v4_prefix(client, batch.base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert page.entries[0].proposal == proposal
    assert page.entries[0].proposal.strategy_number == 17


def test_manager_restore_rejects_weak_seventeen_and_preserves_fourteen():
    from dataclasses import replace
    from types import SimpleNamespace
    from test_backtest_strategy_one_management import _Runtime, _Evidence, _proposal
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner, StrategyOneManagementState
    plans = sources()
    momentum = load_rising_momentum_plan(*plans[:2], client=GrowthSource(1.05))
    proposal = replace(_proposal(), boundary_ms=31_000,
                       strategy_number=17, momentum=momentum.lookup('AAA', 31_000))
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(31_000, ((key, proposal),), (), (), (), ())
    runtime = _Runtime()
    runtime.config = SimpleNamespace(strategy_revision=17)
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=_Evidence(), tick_for_ticker=lambda _: .01)
    with pytest.raises(ValueError, match='momentum'):
        manager.restore_state(state)
    runtime.config = SimpleNamespace(strategy_revision=14)
    inherited = StrategyOneManagementRunner(runtime=runtime, evidence=_Evidence(), tick_for_ticker=lambda _: .01)
    inherited.restore_state(replace(state, submitted=((key, replace(proposal, strategy_number=14)),)))


@pytest.mark.parametrize('relative,before,after', [
    ('trading_runtime/strategy_strong_ten_second_momentum.py',
     'HISTOGRAM_GROWTH_FRACTION = 0.10', 'HISTOGRAM_GROWTH_FRACTION = 0.0'),
    ('trading_runtime/strategy_strong_ten_second_momentum.py',
     '(histogram > 0)', '(histogram >= 0)'),
    ('trading_runtime/strategy_rising_momentum_witness.py',
     'if strategy_number in (17, 18):', 'if strategy_number == 16:'),
    ('backend/backtest_strategy_rising_momentum.py',
     'if strategy_number in (17, 18):', 'if strategy_number == 16:'),
    ('backend/backtest_strategy_one_static_gate.py',
     'momentum_plan.eligible_mask(strategy_number)', 'momentum_plan.eligible_mask()'),
    ('backend/backtest_strategy_one_management.py',
     'numbered_momentum_entry(proposal.momentum, proposal.strategy_number)', 'rising_momentum_entry(proposal.momentum)'),
])
def test_source_certificate_rejects_weaker_gate_and_wrong_number_dispatch(tmp_path, relative, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    source = (Path(__file__).parents[1] / 'src' / relative).read_text(encoding='utf-8')
    assert before in source
    altered = tmp_path / Path(relative).name
    altered.write_text(source.replace(before, after, 1), encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: altered})
