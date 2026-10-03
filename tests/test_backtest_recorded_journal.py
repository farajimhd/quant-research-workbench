"""Recorded presentation verifies stored seals; it does not admit execution."""
from copy import deepcopy
import pytest

from src.backend import backtest_recorded_journal as recorded
from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4
from src.trading_runtime.arte_journal_writer import _canonical_typed_content
from tests.test_arte_journal_commit_v4 import source


def fixture():
    options = source()
    options['status'] = 'completed'
    commit, families = prepare_commit_v4(**options)
    rows = {name: [{**_canonical_typed_content(name, {k: v for k, v in row.items() if k != 'content_hash'}),
                    'content_hash': row['content_hash']} for row in children]
            for name, children in options['sealed_families']}
    return [commit], list(families), rows, options['run_id']


def test_complete_recorded_chain_is_terminal_and_not_a_write_lease():
    args = fixture()
    prefix = recorded.verify_recorded_chain(*args)
    assert prefix.status == 'completed'
    assert prefix.run_id == args[3]
    assert prefix.last_sequence == 1


def test_only_explicit_recorded_reads_use_the_read_workload_lane():
    from src.backend.workload_budget import classify_workload
    for endpoint in ('v4-performance', 'v4-journal-query'):
        path = f'/api/trading/backtest/runs/run/{endpoint}'
        assert classify_workload('GET', path, journal_only=True) == 'runtime_state'
        assert classify_workload('GET', path) == 'simulation'
        assert classify_workload('POST', path, journal_only=True) == 'simulation'
    assert classify_workload('GET', '/api/trading/backtest/runs/run/v4-chart', journal_only=True) == 'simulation'


@pytest.mark.parametrize('damage', ['scalar', 'missing_row', 'duplicate_row', 'family', 'chain', 'running', 'sequence'])
def test_recorded_reader_rejects_corruption_and_incomplete_fences(damage):
    commits, families, rows, run = deepcopy(fixture())
    event = rows['trading_event_v1'][0]
    if damage == 'scalar':
        event['entity_id'] = 'changed'
    elif damage == 'missing_row':
        rows['trading_event_v1'].clear()
    elif damage == 'duplicate_row':
        rows['trading_event_v1'].append(dict(event))
    elif damage == 'family':
        families.clear()
    elif damage == 'chain':
        commits[0]['prior_batch_id'] = commits[0]['batch_id']
    elif damage == 'running':
        options = source()
        commits[0], rebuilt = prepare_commit_v4(**options)
        families = list(rebuilt)
    else:
        event['sequence'] = 2
    with pytest.raises(ValueError):
        recorded.verify_recorded_chain(commits, families, rows, run)


def test_recorded_performance_uses_final_fees_and_the_same_financial_projection(monkeypatch):
    from src.backend import backtest_v4_saved_review as full
    from src.backend import backtest_v4_performance_evidence as evidence
    from tests.test_backtest_v4_saved_review import RUN, Client, _prefix
    monkeypatch.setattr(recorded, 'load_recorded_attestation', lambda *_: {'prefix': _prefix()})
    base = dict(account_id='SIM-01-A', conid=10, ticker='ABC', currency='USD', exchange='SIM',
                broker_order_id='', client_order_id='', strategy_id='early-squeeze-strategy',
                strategy_revision=1, setup='', exit_reason='', signal_price=None, arrival_midpoint=None, planned_risk=None)
    fills = tuple({**base, 'execution_id': identity, 'sequence': sequence, 'side': side,
                   'quantity': '10', 'price': price, 'source_event_time': stamp}
                  for identity, sequence, side, price, stamp in (
                      ('buy', 1, 'B', '2', '2026-08-18 08:00:00'),
                      ('sell', 3, 'S', '3', '2026-08-18 08:01:00')))
    fees = tuple(dict(execution_id=identity, sequence=sequence, account_id='SIM-01-A',
                      commission='1', currency='USD', status='final')
                 for identity, sequence in (('buy', 2), ('sell', 4)))
    for module in (recorded, full):
        monkeypatch.setattr(module, 'load_committed_execution_page', lambda *_a, **_k: fills)
        monkeypatch.setattr(module, 'load_committed_commission_page', lambda *_a, **_k: fees)
        monkeypatch.setattr(module, '_saved_protection_events', lambda *_a: [])
        monkeypatch.setattr(module, '_head_matches', lambda *_a: True)
    monkeypatch.setattr(evidence, 'attach_exit_evidence', lambda *_: None)
    monkeypatch.setattr(full, '_terminal_attestation', lambda *_: {'prefix': _prefix()})
    result = recorded.project_recorded_performance(Client(), RUN)
    assert result == full.load_v4_performance_report(Client(), RUN)
    assert float(result['report']['summary']['net_pnl']) == 8
    monkeypatch.setattr(recorded, 'load_committed_commission_page', lambda *_a, **_k: ())
    with pytest.raises(RuntimeError, match='final fees'):
        recorded.project_recorded_performance(Client(), RUN)
