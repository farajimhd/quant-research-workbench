"""Strategy 9 witness binds an original entry, causal boundary and immutable exit."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.arte_followthrough_failure_v4 import (
    FAILURE, V4FollowThroughFailureBatch, project_followthrough_failure,
    seal_followthrough_rows, restore_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from tests.test_arte_journal_writer import MemoryClient


class AncestryMemoryClient(MemoryClient):
    """Model the bounded ancestry query's predicates in the SQL test adapter."""
    def __init__(self):
        super().__init__()
        self.queries = []

    def execute(self, sql):
        if sql.startswith('SELECT batch_id,prior_batch_id,first_sequence,last_sequence '):
            import json
            import re
            self.queries.append(sql)
            run_id = re.search(r"WHERE run_id='([^']+)'", sql).group(1)
            lower = int(re.search(r'first_sequence>=(\d+)', sql).group(1))
            upper = int(re.search(r'last_sequence<=(\d+)', sql).group(1))
            limit = int(re.search(r'LIMIT (\d+)', sql).group(1))
            rows = sorted((r for r in self.tables.get('trading_commit_v4', ())
                if r['run_id'] == run_id and int(r['first_sequence']) >= lower
                and int(r['last_sequence']) <= upper), key=lambda r: int(r['first_sequence']))
            columns = ('batch_id', 'prior_batch_id', 'first_sequence', 'last_sequence')
            return '\n'.join(json.dumps({k: r[k] for k in columns}) for r in rows[:limit])
        return super().execute(sql)


def fixture(strategy_number=9, witness=None):
    witness = witness or FollowThroughFailure(40000, 31100, 10.01, 9.89, 99400,
                                  -.02, -.01, 9.94, 9.95, 100000)
    financial = StrategyOneFinancialView('assignment-1', 'DU1', 'AAA',
        AssignmentStatus.WATCHING, StrategyPermissions(observe=True, enter=True),
        10., False, False, False, 1)
    source_id = str(UUID(int=77))
    intent = followthrough_exit_intent(witness, financial,
        session_date=date(2026, 8, 18), source_entry_intent_id=source_id)
    base = strategy_intent_batch(intent, run_id=str(UUID(int=1)), run_month=date(2026, 8, 1),
        account_id='DU1', attempt_id=str(UUID(int=2)), batch_id=str(UUID(int=3)),
        prior_batch_id=str(UUID(int=0)), sequence=2, source_cursor='cursor',
        run_status='running', recorded_at=intent.event_time)
    row = project_followthrough_failure(witness, intent, source_id,
        run_id=base.run_id, batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'], assignment_id=financial.assignment_id,
        strategy_number=strategy_number)
    families = dict(_sealed_families(base))
    source = {**families['trading_strategy_intent_v1'][0],
        'record_id': str(UUID(int=10)), 'intent_id': source_id, 'action': 'enter_long',
        'reason': 'strategy_one_entry', 'reference_price': 10.01, 'invalidation_price': 9.89}
    source_event = {**base.events[0], 'record_id': source['record_id'], 'sequence': 1}
    entry = {'parent_record_id': source['record_id'], 'strategy_number': strategy_number,
             'assignment_id': financial.assignment_id, 'boundary_ms': 31000}
    return witness, intent, base, row, source, source_event, entry


def test_scalar_family_and_deterministic_factory():
    witness, intent, base, row, *_ = fixture()
    assert intent.metadata == {} and intent.reason == 'strategy_nine_followthrough_failure'
    assert intent.quantity == 10 and intent.reference_price == 9.94
    assert 'live_market_ssd' in FAILURE.ddl()
    assert not any('json' in k or 'blob' in k for k, _ in FAILURE.columns)
    assert restore_failure(row) == witness
    unit = V4FollowThroughFailureBatch(base, row)
    with pytest.raises(TypeError):
        unit.failure['bid'] = 9.


@pytest.mark.parametrize('field,value', [('reference_ask', 10.02), ('initial_stop', 9.88),
    ('assignment_id', 'wrong'), ('boundary_ms', 45000), ('strategy_number', 8),
    ('first_held_boundary_ms', 39000)])
def test_entire_graph_rejects_changed_scalar(field, value):
    _, _, base, row, source, source_event, entry = fixture()
    families = dict(_sealed_families(base))
    intents = (source, *families['trading_strategy_intent_v1'])
    events = (source_event, *base.events)
    assert len(seal_followthrough_rows(None, (row,), intents, events, (entry,))) == 1
    with pytest.raises(ValueError):
        seal_followthrough_rows(None, ({**row, field: value},), intents, events, (entry,))


def test_missing_or_duplicate_witness_fails_closed():
    _, _, base, row, source, source_event, entry = fixture()
    intents = (source, *dict(_sealed_families(base))['trading_strategy_intent_v1'])
    for rows in ((), (row, row)):
        with pytest.raises(ValueError):
            seal_followthrough_rows(None, rows, intents, (source_event, *base.events), (entry,))


@pytest.mark.parametrize('strategy_number', [9, 10, 11, 12])
def test_failure_source_binds_its_exact_successor_number(strategy_number):
    _, _, base, row, source, source_event, entry = fixture(strategy_number)
    intents = (source, *dict(_sealed_families(base))['trading_strategy_intent_v1'])
    assert seal_followthrough_rows(None, (row,), intents, (source_event, *base.events), (entry,))
    other_number = 10 if strategy_number == 9 else 9
    with pytest.raises(ValueError, match='original entry or exit'):
        seal_followthrough_rows(None, ({**row, 'strategy_number': other_number},),
            intents, (source_event, *base.events), (entry,))


@pytest.mark.parametrize('strategy_number', [8, 15, True])
def test_projector_rejects_unapproved_failure_consumers(strategy_number):
    witness, intent, base, *_ = fixture()
    with pytest.raises(ValueError, match='Strategy 9'):
        project_followthrough_failure(witness, intent, str(UUID(int=77)),
            run_id=base.run_id, batch_id=base.batch_id,
            parent_record_id=base.events[0]['record_id'], assignment_id='assignment-1',
            strategy_number=strategy_number)


@pytest.mark.parametrize("strategy_number", [9, 10, 11, 12])
def test_memory_retry_preserves_exact_witness(strategy_number):
    witness, intent, base, _, *_ = fixture(strategy_number)
    journal = BacktestMemoryJournal(run_id=base.run_id)
    kwargs = dict(intent=intent, witness=witness, source_entry_intent_id=str(UUID(int=77)),
                  account_id='DU1', strategy_id='early-squeeze-strategy', strategy_revision=strategy_number)
    record = journal.append_followthrough_exit(**kwargs)
    assert journal.followthrough_exit_for_record(record.record_id) == (intent, witness, str(UUID(int=77)))
    with pytest.raises(ValueError, match='immutable witness'):
        journal.append_followthrough_exit(**{**kwargs, 'witness': replace(witness, quote_age_us=200000)})
    with pytest.raises(ValueError, match='immutable witness'):
        journal.append_followthrough_exit(**{**kwargs, 'strategy_revision': 10 if strategy_number == 9 else 9})


@pytest.mark.parametrize("strategy_number", [11, 12])
@pytest.mark.parametrize('first_held_ms,eligible', [(40000, True), (39900, False)])
def test_strategy_eleven_persistence_inclusive_first_minute(first_held_ms, eligible, strategy_number):
    # The unbounded inherited factory can create both intents. Strategy 11
    # must independently reject the forged late witness at every authority.
    original = fixture()[0]
    witness = replace(original, boundary_ms=100000, first_held_boundary_ms=first_held_ms)
    _, intent, base, row, source, source_event, entry = fixture(10, witness)
    journal = BacktestMemoryJournal(run_id=base.run_id)
    kwargs = dict(intent=intent, witness=witness, source_entry_intent_id=str(UUID(int=77)),
        account_id='DU1', strategy_id='early-squeeze-strategy', strategy_revision=strategy_number)
    row = {**row, 'strategy_number': strategy_number}
    entry = {**entry, 'strategy_number': strategy_number}
    intents = (source, *dict(_sealed_families(base))['trading_strategy_intent_v1'])
    calls = (
        lambda: journal.append_followthrough_exit(**kwargs),
        lambda: restore_failure(row),
        lambda: project_followthrough_failure(witness, intent, str(UUID(int=77)),
            run_id=base.run_id, batch_id=base.batch_id, parent_record_id=base.events[0]['record_id'],
            assignment_id='assignment-1', strategy_number=strategy_number),
        lambda: seal_followthrough_rows(None, (row,), intents, (source_event, *base.events), (entry,)),
    )
    for call in calls:
        if eligible:
            assert call() is not None
        else:
            with pytest.raises(ValueError, match='first-minute eligibility'):
                call()
    assert journal.pending_record_count == (1 if eligible else 0)


@pytest.mark.parametrize('strategy_number', [9, 10])
def test_original_failure_persistence_retains_unbounded_age(strategy_number):
    witness = replace(fixture()[0], boundary_ms=100000, first_held_boundary_ms=39900)
    _, intent, base, row, source, source_event, entry = fixture(strategy_number, witness)
    intents = (source, *dict(_sealed_families(base))['trading_strategy_intent_v1'])
    assert seal_followthrough_rows(None, (row,), intents, (source_event, *base.events), (entry,))
    assert restore_failure(row) == witness


@pytest.mark.parametrize("compound_mode", [True, False])
@pytest.mark.parametrize("strategy_number", [9, 10, 11, 12])
def test_compound_publishes_full_graph_and_cold_verifies_witness(compound_mode, strategy_number):
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence
    from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_followthrough_failure_v4 import load_followthrough_failure
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    proposal = StrategyOneEntryProposal('assignment-1', 'DU1', 'AAA', 31000, 30000,
        10.01, 9.89, 12., 'R4', .5, 30000, 'support', strategy_number)
    day = date(2026, 8, 18)
    original = strategy_one_entry_intent(proposal, session_date=day)
    witness, _, prior, _, *_ = fixture()
    financial = StrategyOneFinancialView('assignment-1', 'DU1', 'AAA',
        AssignmentStatus.WATCHING, StrategyPermissions(), 10., False, False, False, 1)
    exit_intent = followthrough_exit_intent(witness, financial, session_date=day,
                                         source_entry_intent_id=original.intent_id)
    def base(intent, sequence, batch, previous):
        return strategy_intent_batch(intent, run_id=prior.run_id, run_month=day.replace(day=1),
            account_id='DU1', attempt_id=prior.attempt_id, batch_id=str(UUID(int=batch)),
            prior_batch_id=str(UUID(int=previous)), sequence=sequence, source_cursor='start',
            run_status='running', recorded_at=intent.event_time)
    first, second = base(original, 1, 11, 0), base(exit_intent, 2, 12, 11)
    entry = project_strategy_one_entry_evidence(proposal, original, session_date=day,
        run_id=first.run_id, batch_id=first.batch_id, parent_record_id=first.events[0]['record_id'])
    failure = project_followthrough_failure(witness, exit_intent, original.intent_id,
        run_id=second.run_id, batch_id=second.batch_id, parent_record_id=second.events[0]['record_id'],
        assignment_id=financial.assignment_id, strategy_number=strategy_number)
    compound = coalesce_v4_units((V4StrategyOneEntryBatch(first, (entry,)),
                                 V4FollowThroughFailureBatch(second, failure)))
    client = attached_v4_client(AncestryMemoryClient())
    if compound_mode:
        publish_compound_v4(client, compound)
    else:
        from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4
        _publish_typed_batch_v4(client, first, strategy_one_entry_rows=(entry,))
        _publish_typed_batch_v4(client, second, followthrough_rows=(failure,))
    prefix = load_verified_v4_prefix(client, prior.run_id)
    assert prefix.last_sequence == 2
    recovered, restored = load_followthrough_failure(client, prefix, second.events[0]['record_id'])
    assert restored == witness and recovered['source_entry_intent_id'] == original.intent_id
    assert recovered['strategy_number'] == strategy_number


@pytest.mark.parametrize('count', [1, 3, 512])
def test_ancestry_bulk_read_query_count_is_independent_of_chain_length(count):
    from src.trading_runtime.arte_followthrough_failure_v4 import _verify_source_ancestor_interval
    client = AncestryMemoryClient()
    rows = [dict(run_id='run', batch_id=str(UUID(int=n + 1)),
                 prior_batch_id=str(UUID(int=n)), first_sequence=n + 1, last_sequence=n + 1)
            for n in range(count)]
    client.tables['trading_commit_v4'] = rows
    _verify_source_ancestor_interval(client, 'run', rows[0], rows[-1])
    assert len(client.queries) == 1
    assert 'LIMIT 100001' in client.queries[0]


@pytest.mark.parametrize('fault', ['orphan_source', 'extra_orphan', 'duplicate', 'gap', 'cycle', 'bound'])
def test_ancestry_bulk_read_fails_closed(fault):
    from src.trading_runtime.arte_followthrough_failure_v4 import _verify_source_ancestor_interval
    client = AncestryMemoryClient()
    rows = [dict(run_id='run', batch_id=str(UUID(int=n + 1)),
                 prior_batch_id=str(UUID(int=n)), first_sequence=n + 1, last_sequence=n + 1)
            for n in range(3)]
    source, predecessor = dict(rows[0]), dict(rows[-1])
    if fault == 'orphan_source':
        rows[1]['prior_batch_id'] = str(UUID(int=999))
    elif fault == 'extra_orphan':
        rows.append({**rows[1], 'batch_id': str(UUID(int=999))})
    elif fault == 'duplicate':
        rows.append(dict(rows[1]))
    elif fault == 'gap':
        rows.pop(1)
    elif fault == 'cycle':
        rows[1]['prior_batch_id'] = rows[2]['batch_id']
    client.tables['trading_commit_v4'] = rows
    with pytest.raises(RuntimeError):
        _verify_source_ancestor_interval(client, 'run', source, predecessor,
            max_commits=2 if fault == 'bound' else 100_000)
    assert len(client.queries) == 1
