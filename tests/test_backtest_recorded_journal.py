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


@pytest.mark.parametrize("damage", [None, "code", "clock", "mode", "uninstalled"])
def test_recorded_programme_admission_and_read_profile_use_installed_pins(monkeypatch, damage):
    from tests.test_program_strategy_registry import installed
    from src.trading_runtime import strategy_registry
    from src.backend.backtest_v4_saved_review import _require_declared_read_profile
    from types import SimpleNamespace
    registration, _, _ = installed(monkeypatch)
    context = dict(mode="backtest", strategy_id=registration.strategy_id,
        strategy_revision=registration.revision, code_hash=registration.expected_code_hash,
        evaluation_interval_ms=1000)
    if damage == "code": context["code_hash"] = "f" * 64
    if damage == "clock": context["evaluation_interval_ms"] = 500
    if damage == "mode": context["mode"] = "paper"
    if damage == "uninstalled": monkeypatch.setattr(strategy_registry, "_PROGRAM_REGISTRY", {})
    if damage:
        with pytest.raises(ValueError, match="installed|Backtest|programme executor"):
            recorded.require_recorded_strategy(context)
    else:
        assert recorded.require_recorded_strategy(context) is registration
        assert _require_declared_read_profile(SimpleNamespace(), context) == {"program_decisions": True}
        assert _require_declared_read_profile(SimpleNamespace(program_decision_profile=True), context) == {}
        context["evaluation_interval_ms"] = 100
        assert recorded.require_recorded_strategy(context) is registration


def test_only_explicit_recorded_reads_use_the_read_workload_lane():
    from src.backend.workload_budget import classify_workload
    for endpoint in ('v4-performance', 'v4-journal-query'):
        path = f'/api/trading/backtest/runs/run/{endpoint}'
        assert classify_workload('GET', path, journal_only=True) == 'runtime_state'
        assert classify_workload('GET', path) == 'simulation'
        assert classify_workload('POST', path, journal_only=True) == 'simulation'
    assert classify_workload('GET', '/api/trading/backtest/runs/run/v4-chart', journal_only=True) == 'charts'


@pytest.mark.parametrize('damage', ['scalar', 'missing_row', 'duplicate_row', 'family', 'chain', 'running', 'sequence'])
@pytest.mark.parametrize('streamed', [False, True])
def test_recorded_reader_rejects_corruption_and_incomplete_fences(damage, streamed):
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
        recorded.verify_recorded_chain(commits, families,
            (lambda identity, selected: rows) if streamed else rows, run)


def test_streamed_recorded_chain_matches_the_complete_reader():
    commits, families, rows, run = fixture()
    calls = []
    def read(identity, selected):
        calls.append((identity, selected))
        return rows
    assert recorded.verify_recorded_chain(commits, families, read, run) == recorded.verify_recorded_chain(
        commits, families, rows, run)
    assert calls == [(commits[0]['batch_id'], families)]


@pytest.mark.parametrize('streamed', [False, True])
def test_recorded_sequences_use_verified_uint64_values(streamed):
    commits, families, rows, run = fixture()
    rows['trading_event_v1'][0]['sequence'] = str(rows['trading_event_v1'][0]['sequence'])
    result = recorded.verify_recorded_chain(commits, families,
        (lambda *_: rows) if streamed else rows, run)
    assert result.last_sequence == 1


def test_streamed_recorded_chain_rejects_foreign_batch_rows():
    commits, families, rows, run = fixture()
    rows['trading_event_v1'][0]['batch_id'] = '00000000-0000-0000-0000-000000000099'
    with pytest.raises(ValueError, match='scalar seal'):
        recorded.verify_recorded_chain(commits, families, lambda *_: rows, run)


def test_programme_attestation_streams_more_than_aggregate_row_budget(monkeypatch):
    """Real scalar/family verification; financial anchors use explicit test seams."""
    from uuid import UUID, uuid4
    from types import SimpleNamespace
    from weakref import ref
    from tests.test_program_strategy_registry import installed
    from src.trading_runtime.arte_journal_writer import typed_row
    from src.trading_runtime import arte_backtest_snapshot_anchor, arte_backtest_definition, arte_journal_projection
    from src.backend import backtest_v4_saved_review
    registration, _, _ = installed(monkeypatch)
    options = source()
    run = str(uuid4())
    prototype = dict(options['sealed_families'][0][1][0])
    prototype.pop('content_hash')
    commits, families = [], []
    class Row(dict): pass
    def batch_rows(index):
        rows = []
        for offset in range(4096):
            content = {**prototype, 'run_id': run, 'batch_id': str(UUID(int=index + 100)),
                'sequence': index * 4096 + offset + 1,
                'record_id': str(UUID(int=index * 4096 + offset + 100000))}
            sealed = typed_row('trading_event_v1', content)
            rows.append(Row({**_canonical_typed_content('trading_event_v1', content),
                'content_hash': sealed['content_hash']}))
        return rows
    for index in range(50):
        rows = batch_rows(index)
        commit, selected = prepare_commit_v4(**{**options, 'run_id': run,
            'batch_id': str(UUID(int=index + 100)),
            'prior_batch_id': str(UUID(int=index + 99)) if index else str(UUID(int=0)),
            'first_sequence': index * 4096 + 1, 'last_sequence': (index + 1) * 4096,
            'status': 'completed' if index == 49 else 'running', 'source_cursor': 'start',
            'sealed_families': (('trading_event_v1', rows),)})
        commits.append(commit)
        families.extend(selected)
    del rows
    context = dict(run_id=run, mode='backtest', strategy_id=registration.strategy_id,
        strategy_revision=registration.revision, code_hash=registration.expected_code_hash,
        evaluation_interval_ms=1000, account_ids=('A',), session_date='2026-08-18')
    monkeypatch.setattr(recorded, 'load_typed_run_context', lambda *_: context)
    def inventory(_, sql):
        return families if 'trading_commit_family_v4' in sql else commits
    monkeypatch.setattr(recorded, '_rows', inventory)
    previous_row, reads = None, []
    def details(_, specs, filters):
        nonlocal previous_row
        assert previous_row is None or previous_row() is None
        assert len(filters) < 200 and 'batch_id=toUUID(' in filters
        assert len(specs) == 1 and specs[0][0] == 'trading_event_v1' and specs[0][2] == 4096
        index = len(reads)
        assert str(UUID(int=index + 100)) in filters
        rows = batch_rows(index)
        previous_row = ref(rows[0])
        reads.append(index)
        return {'trading_event_v1': rows}
    monkeypatch.setattr(recorded, '_batched_detail_rows_v4', details)
    monkeypatch.setattr(arte_journal_projection, 'load_latest_backtest_cursor', lambda *_: None)
    monkeypatch.setattr(arte_backtest_snapshot_anchor, 'load_terminal_backtest_snapshot',
        lambda *args, **kwargs: dict(state_hash='a' * 64, state_revision=204800, snapshot_at='2026-08-18T20:00:00Z'))
    monkeypatch.setattr(arte_backtest_definition, 'load_committed_initial_cash', lambda *args, **kwargs: 100000.)
    monkeypatch.setattr(backtest_v4_saved_review, '_terminal_financial_accounts', lambda *_: {})
    monkeypatch.setattr(recorded, '_head_matches', lambda *_: True)
    client = SimpleNamespace(base_url='http://synthetic', user='synthetic-programme', password='synthetic',
        program_decision_profile=True)
    evidence = recorded.load_recorded_attestation(client, run)
    assert evidence['prefix'].status == 'completed' and evidence['prefix'].last_sequence == 204800
    assert reads == list(range(50)) and previous_row() is None


@pytest.mark.parametrize("exact", [False, True])
def test_recorded_performance_uses_final_fees_and_the_same_financial_projection(monkeypatch, exact):
    from datetime import UTC, datetime
    from functools import partial
    from src.trading_runtime import domain
    from src.backend import backtest_v4_saved_review as full
    from src.backend import backtest_v4_performance_evidence as evidence
    from tests.test_backtest_v4_saved_review import RUN, Client, _prefix
    # Both projections construct received_at; freeze the presentation clock
    # so full-record equality tests financial parity rather than elapsed time.
    monkeypatch.setattr(domain, 'Execution', partial(domain.Execution, received_at=datetime(2026, 8, 18, tzinfo=UTC)))
    monkeypatch.setattr(recorded, 'load_recorded_attestation',
        lambda *_: {'prefix': _prefix(), 'exact_prices': exact})
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
    def selected_fills(*args, **scope):
        assert scope.get("exact_prices", False) is exact
        return fills
    monkeypatch.setattr(recorded, 'load_committed_execution_page', selected_fills)
    # The legacy full-review projection remains a separate comparison reader.
    calls = []
    def attach(*args, **scope):
        calls.append(scope.get('exact_prices', False))
    monkeypatch.setattr(evidence, 'attach_exit_evidence', attach)
    monkeypatch.setattr(full, '_terminal_attestation', lambda *_: {'prefix': _prefix()})
    result = recorded.project_recorded_performance(Client(), RUN)
    assert result == full.load_v4_performance_report(Client(), RUN)
    assert calls == [exact, False]
    assert float(result['report']['summary']['net_pnl']) == 8
    monkeypatch.setattr(recorded, 'load_committed_commission_page', lambda *_a, **_k: ())
    with pytest.raises(RuntimeError, match='final fees'):
        recorded.project_recorded_performance(Client(), RUN)


@pytest.mark.parametrize("families,exact", [
    ((), False), (("trading_execution_v1",), False),
    (("trading_strategy_signal_exact_price_v2",), True),
    (("trading_execution_exact_price_v1", "trading_order_command_exact_price_v1"), True),
])
def test_recorded_price_selection_uses_physical_family_seals(families, exact):
    assert recorded.recorded_exact_prices(families) is exact


def test_recorded_price_selection_rejects_mixed_representations():
    with pytest.raises(ValueError, match="incompatible price"):
        recorded.recorded_exact_prices(("trading_execution_exact_price_v1", "trading_strategy_signal_v2"))


def test_recorded_chart_markers_preserve_positions_without_source_audit(monkeypatch):
    from src.backend import backtest_v4_saved_review as full
    from tests.test_backtest_v4_saved_review import RUN, Client, _prefix
    lifecycle = dict(episode_id='episode-1', instrument={'symbol': 'MIMI'}, account_id='SIM-01-A',
        opened_at='2026-09-03T12:00:00+00:00', entry_price=3.0, side='LONG', quantity=100,
        current_quantity=75, status='open', protection_timeline=[])
    monkeypatch.setattr(recorded, 'load_recorded_performance', lambda *_: {
        'run_id': RUN, 'verified_sequence': 2, 'position_lifecycles': [lifecycle,
            {**lifecycle, 'episode_id': 'other', 'instrument': {'symbol': 'OTHER'}}]})
    monkeypatch.setattr(recorded, 'load_recorded_attestation', lambda *_: {'prefix': _prefix()})
    monkeypatch.setattr(recorded, '_head_matches', lambda *_: True)
    monkeypatch.setattr(full, '_terminal_attestation', lambda *_: pytest.fail('Chart triggered the full source audit'))
    monkeypatch.setattr('src.trading_runtime.arte_intent_projection.load_committed_strategy_intent_page', lambda *_a, **_k: ())
    result = recorded.load_recorded_chart_trades(Client(), RUN, 'mimi')
    assert result['ticker'] == 'MIMI'
    assert len(result['position_lifecycles']) == 1
    assert result['position_lifecycles'][0]['current_quantity'] == 75
    assert result['position_lifecycles'][0]['status'] == 'open'
    monkeypatch.setattr(recorded, '_head_matches', lambda *_: False)
    with pytest.raises(RuntimeError, match='head changed'):
        recorded.load_recorded_chart_trades(Client(), RUN, 'MIMI')
