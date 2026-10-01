"""Native20 typed journal round trips; fixtures perform no operational DDL."""
from dataclasses import replace
from datetime import date
import json
import re
import struct
from uuid import UUID

import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from test_strategy_one_intent import _proposal
from tests.test_arte_journal_writer import MemoryClient
from tests.test_arte_journal_commit_v4 import attached_v4_client
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import (
    compile_certified_price_break_plan, bind_certified_price_break_proposal,
    certified_price_entry_intent, project_certified_price_entry, CertifiedPriceReadbackAuthority,
)
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM, VALUES, project_initial_momentum_entry
from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM, project_rising_momentum_entry
from src.trading_runtime.arte_first_price_entry_v4 import FIRST_PRICE
from src.trading_runtime.arte_strategy_one_entry_journal import (
    project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page,
    load_committed_strategy_one_source,
)
from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch, typed_row
from src.trading_runtime.arte_journal_commit_v4 import publish_strategy_one_entry_batch_v4, load_verified_v4_prefix
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent


class ExactBits(MemoryClient):
    def execute(self, sql):
        for contract in (MOMENTUM, INITIAL_MOMENTUM):
            if f'FROM arte.{contract.name} ' in sql and 'reinterpretAsUInt64' in sql:
                raw = super().execute(re.sub(r'SELECT .*? FROM arte\.',
                    'SELECT ' + ','.join(name for name, _ in contract.columns) + ' FROM arte.', sql, count=1))
                rows = [json.loads(line) for line in raw.splitlines() if line]
                for row in rows:
                    for name in VALUES:
                        row[name + '_bits'] = (None if row[name] is None else
                            int.from_bytes(struct.pack('>d', row[name]), 'big'))
                return '\n'.join(json.dumps(row) for row in rows)
        return super().execute(sql)


def prepared_entry(source, sequence, boundary, prior, *, strategy_number=20):
    plan = source.plan
    original = replace(_proposal(), strategy_number=18 if strategy_number in (26, 27, 28, 29, 30, 31) else 19, boundary_ms=boundary,
        momentum=plan.momentum.lookup('AAA', boundary),
        initial_momentum=plan.source.parent.selection_witness('AAA', boundary))
    proposal = bind_certified_price_break_proposal(plan, original, strategy_number=strategy_number)
    intent = certified_price_entry_intent(plan, proposal, session_date=date(2026, 8, 18))
    inherited = strategy_one_entry_intent(original, session_date=date(2026, 8, 18))
    assert replace(intent, intent_id=inherited.intent_id) == inherited
    base = strategy_intent_batch(intent, run_id=source.run_id, run_month=date(2026, 8, 1),
        account_id=proposal.account_id, attempt_id=str(UUID(int=51)), batch_id=str(UUID(int=60 + sequence)),
        prior_batch_id=prior, sequence=sequence, source_cursor=f'2026-08-18:{boundary}',
        run_status='running', recorded_at=intent.event_time)
    scope = dict(run_id=source.run_id, batch_id=base.batch_id, parent_record_id=base.intents[0]['record_id'])
    evidence = project_strategy_one_entry_evidence(proposal, intent, session_date=date(2026, 8, 18),
        first_price_source=source, **scope)
    price = project_certified_price_entry(plan, proposal, event_month='2026-08-01', **scope)
    unit = V4StrategyOneEntryBatch(base, (evidence,),
        momentum_evidence=project_rising_momentum_entry(proposal, event_month='2026-08-01', **scope),
        initial_momentum_evidence=project_initial_momentum_entry(proposal, proposal.initial_momentum,
            event_month='2026-08-01', **scope),
        first_price_evidence=price.rows, first_price_authorities=(price.authority,))
    return unit, proposal, intent


@pytest.mark.parametrize('compound', [False, True])
@pytest.mark.parametrize('strategy_number', [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31])
def test_staged_twenty_typed_publication_and_cold_entry_roundtrip(compound, strategy_number):
    if strategy_number in (26, 27, 28, 29, 30, 31):
        from test_backtest_strategy_ten_percent_price_source import authority as relaxed_authority
        market, parent = relaxed_authority()
    else:
        market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority('twenty-cold', plan)
    first, proposal, intent = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=strategy_number)
    second, second_proposal, second_intent = prepared_entry(source, 2, 41000, first.base.batch_id, strategy_number=strategy_number)
    client = attached_v4_client(ExactBits())
    if compound:
        merged = coalesce_v4_units((first, second))
        publish_compound_v4(client, merged)
    else:
        for unit in (first, second):
            publish_strategy_one_entry_batch_v4(client, unit.base, entry_evidence=unit.entry_evidence,
                momentum_evidence=unit.momentum_evidence, initial_momentum_evidence=unit.initial_momentum_evidence,
                first_price_evidence=unit.first_price_evidence, first_price_authorities=unit.first_price_authorities)
    assert len(client.tables[FIRST_PRICE.name]) == 2
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    page = load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
    assert tuple(entry.proposal for entry in page.entries) == (proposal, second_proposal)
    assert tuple(entry.intent for entry in page.entries) == (intent, second_intent)
    for index, unit in enumerate((first, second)):
        rebuilt, recovered = load_committed_strategy_one_source(client, prefix, page.entries[index], first_price_source=source)
        if compound:
            from src.backend.backtest_typed_publisher import _committed_intent_source
            expected = _committed_intent_source(merged.base, unit.base.events[0]['record_id'])
        else:
            expected = unit.base
        assert rebuilt == expected and recovered == page.entries[index].intent
    with pytest.raises((ValueError, RuntimeError)):
        load_verified_v4_prefix(client, source.run_id)
    with pytest.raises((ValueError, RuntimeError)):
        load_committed_strategy_one_entry_page(client, prefix)
    row = client.tables[FIRST_PRICE.name][0]
    forged = typed_row(FIRST_PRICE.name,
        dict({key: value for key, value in row.items() if key != 'content_hash'}, current_close_int=102))
    client.tables[FIRST_PRICE.name][0] = forged
    with pytest.raises(RuntimeError) as rejected:
        load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    assert 'certified source authority' in str(rejected.value.__cause__)
    with pytest.raises(ValueError, match='certified source authority'):
        load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)


@pytest.mark.parametrize('held', [False, True])
@pytest.mark.parametrize('strategy_number', [20, 26, 27, 28, 29, 30, 31])
def test_twenty_manager_scalar_snapshot_recovers_witnesses_from_real_typed_entry(held, strategy_number):
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime.strategy_one_management_snapshot import (
        project_manager_snapshot, restore_manager_snapshot, attach_committed_momentum_sources,
    )
    from src.trading_runtime.strategy_one_position import ProtectionState
    from tests.test_strategy_thirteen_manager_sources import manager
    if strategy_number in (26, 27, 28, 29, 30, 31):
        from test_backtest_strategy_ten_percent_price_source import authority as relaxed_authority
        market, parent = relaxed_authority()
    else:
        market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority('twenty-manager', plan)
    unit, proposal, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=strategy_number)
    client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(client, unit.base, entry_evidence=unit.entry_evidence,
        momentum_evidence=unit.momentum_evidence, initial_momentum_evidence=unit.initial_momentum_evidence,
        first_price_evidence=unit.first_price_evidence, first_price_authorities=unit.first_price_authorities)
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(42000, ((key, proposal),),
        ((key, ProtectionState(42000, proposal.initial_stop, proposal.initial_target)),) if held else (),
        (), ((key, 100100),) if held else (), (), ((key, 31100),) if held else ())
    rows = project_manager_snapshot(run_id=source.run_id, session_date=date(2026, 8, 18),
        checkpoint_sequence=1, state=state, first_price_source=source)
    reference = restore_manager_snapshot(rows)
    assert reference.submitted[0][1].first_price is None
    assert reference.submitted[0][1].momentum is None
    restored = attach_committed_momentum_sources(client, prefix, reference, first_price_source=source)
    assert restored == state
    runner = manager()  # Existing manager fixture; release admission is tested separately.
    runner.runtime.run_id = source.run_id
    runner.restore_state(restored, first_price_source=source)
    assert runner.capture_state(boundary_ms=42000) == state
    with pytest.raises(ValueError, match='native source context'):
        project_manager_snapshot(run_id=source.run_id, session_date=date(2026, 8, 18), checkpoint_sequence=1, state=state)
    with pytest.raises(ValueError, match='native source context'):
        manager().restore_state(state)


@pytest.mark.parametrize('node', ['_project_manager_snapshot_scalar', 'restore_manager_snapshot'])
def test_scalar_recovery_encoding_remains_bound_to_reviewed_source(tmp_path, node):
    import ast
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    assert len(certify_rising_momentum_entry_source()) == 64
    relative = 'trading_runtime/strategy_one_management_snapshot.py'
    original = (Path(__file__).parents[1] / 'src' / relative).read_text(encoding='utf-8')
    tree = ast.parse(original)
    target = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == node)
    target.body.append(ast.Raise(exc=ast.Call(func=ast.Name(id='RuntimeError', ctx=ast.Load()),
        args=[ast.Constant(value='changed recovery contract')], keywords=[]), cause=None))
    altered = tmp_path / 'changed_snapshot.py'
    altered.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: altered})


@pytest.mark.parametrize('strategy_number', [20, 26, 27, 28, 29, 30, 31])
def test_native_twenty_memory_prefix_projects_and_publishes_complete_entry(strategy_number):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    if strategy_number in (26, 27, 28, 29, 30, 31):
        from test_backtest_strategy_ten_percent_price_source import authority as relaxed_authority
        market, parent = relaxed_authority()
    else:
        market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority(str(UUID(int=101)), plan)
    _, proposal, intent = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=strategy_number)
    journal = BacktestMemoryJournal(run_id=source.run_id)
    entry_args = dict(intent=intent, proposal=proposal, session_date=date(2026, 8, 18),
        account_id=proposal.account_id, strategy_id='early-squeeze-strategy', strategy_revision=strategy_number)
    with pytest.raises(ValueError, match='native price source'):
        journal.append_strategy_one_intent(**entry_args)
    foreign = CertifiedPriceReadbackAuthority(str(UUID(int=103)), plan)
    with pytest.raises(ValueError, match='native price source'):
        journal.append_strategy_one_intent(**entry_args, first_price_source=foreign)
    with pytest.raises(ValueError):
        journal.append_strategy_one_intent(**dict(entry_args,
            proposal=replace(proposal, first_price=replace(proposal.first_price, current_close_int=102000))),
            first_price_source=source)
    assert journal.pending_record_count == 0
    journal.append_strategy_one_intent(**entry_args, first_price_source=source)
    scope = dict(attempt_id=str(UUID(int=102)), run_month=date(2026, 8, 1),
        prior_sequence=0, through_sequence=1,
        expected_config={'strategy_id': 'early-squeeze-strategy', 'strategy_revision': strategy_number})
    with pytest.raises(ValueError, match='native price source'):
        project_pending_backtest_v4_prefix(journal, **scope)
    with pytest.raises(ValueError, match='differs from its run'):
        project_pending_backtest_v4_prefix(journal, **scope, first_price_source=foreign)
    units = project_pending_backtest_v4_prefix(journal, **scope, first_price_source=source)
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
    from tests.test_backtest_typed_publisher import FakeWriter
    writer = FakeWriter()
    writer.run_id, writer.journal_profile = source.run_id, 'backtest_v4'
    publisher = BacktestTypedJournalPublisher(journal, writer,
        attempt_id=scope['attempt_id'], run_month=scope['run_month'],
        expected_config=scope['expected_config'], fixed_market_parent_plan=market)
    with pytest.raises(ValueError, match='unbound run'):
        publisher.bind_first_price_source(foreign)
    publisher.bind_first_price_source(source)
    with pytest.raises(ValueError, match='unbound run'):
        publisher.bind_first_price_source(source)
    assert publisher._prepare_batches(1) == units
    assert len(units) == 1 and type(units[0]) is V4StrategyOneEntryBatch
    unit = units[0]
    assert len(unit.first_price_evidence) == len(unit.first_price_authorities) == 1
    assert unit.momentum_evidence and unit.initial_momentum_evidence
    client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(client, unit.base, entry_evidence=unit.entry_evidence,
        momentum_evidence=unit.momentum_evidence, initial_momentum_evidence=unit.initial_momentum_evidence,
        first_price_evidence=unit.first_price_evidence, first_price_authorities=unit.first_price_authorities)
    prefix = load_verified_v4_prefix(client, source.run_id, first_price_source=source)
    page = load_committed_strategy_one_entry_page(client, prefix, first_price_source=source)
    assert page.entries[0].proposal == proposal
    assert page.entries[0].intent == intent
    assert journal.pending_record_count == 1  # Projection/publication does not acknowledge this buffer.
    # A run created in September may trade an August historical session.
    later_publisher = BacktestTypedJournalPublisher(journal, writer,
        attempt_id=scope['attempt_id'], run_month=date(2026, 9, 1),
        expected_config=scope['expected_config'], fixed_market_parent_plan=market)
    later_publisher.bind_first_price_source(source)
    later = later_publisher._prepare_batches(1)[0]
    assert later.base.run_month == date(2026, 9, 1)
    assert later.first_price_evidence[0]['event_month'] == '2026-08-01'
    later_client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(later_client, later.base,
        entry_evidence=later.entry_evidence, momentum_evidence=later.momentum_evidence,
        initial_momentum_evidence=later.initial_momentum_evidence,
        first_price_evidence=later.first_price_evidence,
        first_price_authorities=later.first_price_authorities)
    later_prefix = load_verified_v4_prefix(later_client, source.run_id, first_price_source=source)
    assert load_committed_strategy_one_entry_page(
        later_client, later_prefix, first_price_source=source).entries[0].proposal == proposal


def test_runtime_native_entry_intent_reuses_bound_source_and_rejects_rebinding():
    from types import SimpleNamespace
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority(str(UUID(int=201)), plan)
    _, proposal, intent = prepared_entry(source, 1, 31000, str(UUID(int=0)))
    runtime = TradingRuntime.__new__(TradingRuntime)
    runtime.run_id = source.run_id
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, strategy_revision=20,
        strategy_id='early-squeeze-strategy', anchor_date=date(2026, 8, 18))
    runtime.journal = BacktestMemoryJournal(run_id=source.run_id)
    runtime._strategy_one_price_source = None
    with pytest.raises(ValueError, match='native source'):
        runtime._strategy_one_entry_intent(proposal)
    with pytest.raises(ValueError, match='unbound session'):
        runtime.bind_strategy_one_price_source(CertifiedPriceReadbackAuthority('foreign', plan))
    runtime.bind_strategy_one_price_source(source)
    assert runtime._strategy_one_entry_intent(proposal) == intent
    with pytest.raises(ValueError, match='unbound session'):
        runtime.bind_strategy_one_price_source(source)
    with pytest.raises(ValueError):
        runtime._strategy_one_entry_intent(replace(proposal, initial_stop=11.0))


def test_manager_writer_transports_native_source_through_actual_queue(monkeypatch):
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority(str(UUID(int=301)), plan)
    unit, proposal, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)))
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(42000, ((key, proposal),), (), ())
    client = attached_v4_client(ExactBits())
    client.close = lambda: None
    client.manager_keeper_session = object()
    monkeypatch.setattr(writer_module, '_v4_preflight', lambda _: None)
    monkeypatch.setattr(writer_module, '_verify_run_identity',
        lambda *_: {'mode': 'backtest', 'account_ids': (proposal.account_id,)})
    seen = []
    def publication(got_client, session, rows, *, journal_batch_id, first_price_source):
        assert got_client is client and session is client.manager_keeper_session
        assert first_price_source is source and journal_batch_id == unit.base.batch_id
        assert rows.sources[0]['strategy_number'] == 20
        reference = snapshots.restore_manager_snapshot(rows)
        assert reference.submitted[0][1].first_price is None
        seen.append(rows.snapshot['content_hash'])
    monkeypatch.setattr(snapshots, 'publish_manager_snapshot', publication)
    writer = writer_module.ArteJournalWriter(client, run_id=source.run_id,
        journal_profile='backtest_v4', coalesce_batches=False)
    try:
        writer._last_commit_id = unit.base.batch_id
        args = dict(session_date=date(2026, 8, 18), checkpoint_sequence=1,
            journal_batch_id=unit.base.batch_id, state=state)
        with pytest.raises(ValueError, match='native session source'):
            writer.submit_manager_snapshot(**args)
        assert writer.submit_manager_snapshot(**args, first_price_source=source).result(timeout=5) == unit.base.batch_id
        assert len(seen) == 1
    finally:
        writer.close()


@pytest.mark.parametrize('kind', ('broker_match', 'oms_observation', 'evidence', 'campaign'))
@pytest.mark.parametrize('strategy_number', [20, 26, 27, 28, 29, 30, 31])
def test_checkpoint_writer_transports_native_source_through_actual_queue(monkeypatch, kind, strategy_number):
    import importlib
    from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.domain import TradingMode
    if strategy_number in (26, 27, 28, 29, 30, 31):
        from test_backtest_strategy_ten_percent_price_source import authority as relaxed_authority
        market, parent = relaxed_authority()
    else:
        market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority(str(UUID(int=302)), plan)
    day = date(2026, 8, 18)
    batch_id = str(UUID(int=303))
    module = importlib.import_module(f'src.trading_runtime.strategy_one_{kind}_snapshot')
    client = attached_v4_client(ExactBits())
    client.close = lambda: None
    client.manager_keeper_session = object()
    monkeypatch.setattr(writer_module, '_v4_preflight', lambda _: None)
    monkeypatch.setattr(writer_module, '_verify_run_identity',
        lambda *_: {'mode': 'backtest', 'account_ids': ('DU1',)})
    seen = []
    def publication(got_client, session, rows, **kwargs):
        assert got_client is client and session is client.manager_keeper_session
        assert kwargs['first_price_source'] is source
        root = rows.root if kind == 'oms_observation' else rows.snapshot
        assert root['run_id'] == source.run_id and root['checkpoint_sequence'] == 1
        seen.append(root['content_hash'])
    monkeypatch.setattr(module, f'publish_{kind}_snapshot', publication)
    args = dict(session_date=day, checkpoint_sequence=1, journal_batch_id=batch_id)
    if kind == 'broker_match':
        broker = SimulatedBrokerAdapter(['DU1'], mode=TradingMode.BACKTEST,
            initial_time=market_day_boundary(day, 0), fixed_bar_mode=True)
        args.update(boundary_ms=42000, state=broker.broker_match_snapshot_state())
    elif kind == 'oms_observation':
        args.update(boundary_ms=42000, groups={})
    elif kind == 'evidence':
        args['state'] = StrategyOneEvidenceState(42000, (), (), ())
    else:
        args.update(boundary_ms=42000, ownership=())
    writer = writer_module.ArteJournalWriter(client, run_id=source.run_id,
        journal_profile='backtest_v4', coalesce_batches=False)
    try:
        writer._last_commit_id = batch_id
        submit = getattr(writer, f'submit_{kind}_snapshot')
        for wrong in (object(), CertifiedPriceReadbackAuthority('foreign-run', plan)):
            with pytest.raises(ValueError, match='native session source'):
                submit(**args, first_price_source=wrong)
        with pytest.raises(ValueError, match='native session source'):
            submit(**{**args, 'session_date': date(2026, 8, 19)}, first_price_source=source)
        assert seen == []
        assert submit(**args, first_price_source=source).result(timeout=5) == batch_id
        assert len(seen) == 1
    finally:
        writer.close()


@pytest.mark.parametrize('kind', ('broker_match', 'oms_observation', 'evidence', 'campaign'))
@pytest.mark.parametrize('strategy_number', [24, 26, 27, 28, 29, 30, 31])
def test_cold_checkpoint_reader_verifies_native_entry_prefix(monkeypatch, kind, strategy_number):
    import importlib
    from types import SimpleNamespace
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
    from src.trading_runtime import arte_journal_projection
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.domain import TradingMode
    if strategy_number in (26, 27, 28, 29, 30, 31):
        from test_backtest_strategy_ten_percent_price_source import authority as relaxed_authority
        market, parent = relaxed_authority()
    else:
        market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority(str(UUID(int=304)), plan)
    unit, _, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=strategy_number)
    client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(client, unit.base, entry_evidence=unit.entry_evidence,
        momentum_evidence=unit.momentum_evidence, initial_momentum_evidence=unit.initial_momentum_evidence,
        first_price_evidence=unit.first_price_evidence, first_price_authorities=unit.first_price_authorities)
    module = importlib.import_module(f'src.trading_runtime.strategy_one_{kind}_snapshot')
    day = date(2026, 8, 18)
    args = dict(run_id=source.run_id, session_date=day, checkpoint_sequence=1)
    if kind == 'broker_match':
        broker = SimulatedBrokerAdapter(['DU1'], mode=TradingMode.BACKTEST,
            initial_time=market_day_boundary(day, 0), fixed_bar_mode=True)
        rows = module.project_broker_match_snapshot(**args, boundary_ms=31000,
            state=broker.broker_match_snapshot_state())
        head_type, loader = module.BrokerMatchHead, 'load_unattested_broker_match_snapshot'
    elif kind == 'oms_observation':
        rows = module.project_oms_observation_snapshot(**args, boundary_ms=31000, groups={})
        head_type, loader = module.OmsObservationHead, 'load_unattested_oms_observation_snapshot'
    elif kind == 'evidence':
        state = StrategyOneEvidenceState(31000, (), (), ())
        rows = module.project_evidence_snapshot(**args, state=state)
        head_type, loader = module.EvidenceSnapshotHead, 'load_unattested_evidence_snapshot_rows'
    else:
        rows = module.project_campaign_snapshot(**args, boundary_ms=31000,
            journal_batch_id=unit.base.batch_id, ownership=())
        head_type, loader = module.CampaignSnapshotHead, 'load_campaign_snapshot'
    root = rows.root if kind == 'oms_observation' else rows.snapshot
    head = head_type(source.run_id, 1, unit.base.batch_id, root['content_hash'], 0)
    keeper = SimpleNamespace(read_head=lambda **_: head)
    # Snapshot-row and cursor transport have dedicated publication tests. This
    # fixture exercises each reader's actual complete native entry-prefix seal.
    monkeypatch.setattr(module, loader, lambda *_, **__: rows)
    monkeypatch.setattr(arte_journal_projection, 'load_latest_backtest_cursor',
        lambda *_: dict(run_id=source.run_id, event_sequence=1, batch_id=unit.base.batch_id,
            boundary_ms=31000, session_date=day.isoformat()))
    read = getattr(module, f'load_attested_{kind}_snapshot')
    read_args = dict(run_id=source.run_id, checkpoint_sequence=1)
    with pytest.raises((ValueError, RuntimeError)):
        read(client, keeper, **read_args)
    with pytest.raises((ValueError, RuntimeError)):
        read(client, keeper, **read_args,
            first_price_source=CertifiedPriceReadbackAuthority('foreign-run', plan))
    assert read(client, keeper, **read_args, first_price_source=source) == (state if kind == 'evidence' else rows)
    original = client.tables[FIRST_PRICE.name][0]
    client.tables[FIRST_PRICE.name][0] = typed_row(FIRST_PRICE.name, dict(
        {k: v for k, v in original.items() if k != 'content_hash'}, current_close_int=102))
    with pytest.raises((ValueError, RuntimeError)):
        read(client, keeper, **read_args, first_price_source=source)


def test_native_twenty_terminal_cold_verification_requires_original_source(monkeypatch):
    from datetime import datetime, timezone
    from uuid import uuid4
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_terminal_broker_snapshot_v4 import project_v4_terminal_broker_batch
    from src.backend.backtest_terminal_snapshot_v2 import ACCOUNT_METRICS, position_set_sha256
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchors
    from src.trading_runtime.arte_journal_commit_v4 import publish_terminal_typed_batch_v4
    from tests.test_arte_journal_commit_v4 import captured

    market, parent = authority()
    source = CertifiedPriceReadbackAuthority('native-twenty-terminal',
        compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars())))
    entry, proposal, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)))
    client = attached_v4_client(ExactBits())
    publish_strategy_one_entry_batch_v4(client, entry.base,
        entry_evidence=entry.entry_evidence, momentum_evidence=entry.momentum_evidence,
        initial_momentum_evidence=entry.initial_momentum_evidence,
        first_price_evidence=entry.first_price_evidence,
        first_price_authorities=entry.first_price_authorities)
    at = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)
    journal = BacktestMemoryJournal(run_id=source.run_id, initial_sequence=1)
    account = {name: {'amount': 1000.0, 'currency': 'USD', 'timestamp': 123}
               for name, _ in ACCOUNT_METRICS}
    try:
        journal.append(run_id=source.run_id, category='snapshot', entity_type='portfolio',
            entity_id=proposal.account_id, account_id=proposal.account_id, event_time=at,
            payload={**account, 'snapshot_id': str(uuid4()), 'expected_position_count': 0,
                     'position_set_sha256': position_set_sha256(())})
        journal.append(run_id=source.run_id, category='lifecycle', entity_type='run',
            entity_id=source.run_id, event_time=at,
            payload={'status': 'completed', 'processed_events': 3})
        terminal = project_v4_terminal_broker_batch(tuple(journal.unfenced_records()),
            run_id=source.run_id, account_ids=(proposal.account_id,), attempt_id=str(uuid4()),
            run_month=date(2026, 8, 1), prior_batch_id=entry.base.batch_id,
            source_cursor='2026-08-18:19800000')
    finally:
        journal.close()
    capture = replace(captured(), run_id=source.run_id, account_id=proposal.account_id,
                      state_revision=3, snapshot_at=at)
    monkeypatch.setattr(writer_module, 'load_typed_run_context', lambda *_:
        {'mode': 'backtest', 'account_ids': (proposal.account_id,),
         'strategy_revision': 20, 'session_date': '2026-08-18'})
    anchored = []
    monkeypatch.setattr(anchors, '_publish_terminal_snapshots_after_verified_prefix',
        lambda _client, prefix, captures, context: anchored.append(prefix))
    with pytest.raises((ValueError, RuntimeError)):
        publish_terminal_typed_batch_v4(client, terminal.base, captures=(capture,),
                                      broker_snapshots=terminal.broker_snapshots)
    assert not anchored
    prefix = publish_terminal_typed_batch_v4(client, terminal.base, captures=(capture,),
        broker_snapshots=terminal.broker_snapshots, first_price_source=source)
    assert prefix.status == 'completed' and prefix.last_sequence == 3
    assert len(anchored) == 1
    assert load_committed_strategy_one_entry_page(
        client, prefix, first_price_source=source).entries[0].proposal == proposal


def test_twenty_one_rejects_rehashed_twenty_price_companion():
    from src.trading_runtime.arte_first_price_entry_v4 import seal_first_price_rows
    market, parent = authority()
    source = CertifiedPriceReadbackAuthority('twenty-one-cross-number',
        compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars())))
    unit, _, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=21)
    original = unit.first_price_evidence[0]
    changed = typed_row(FIRST_PRICE.name, {
        **{key: value for key, value in original.items() if key != 'content_hash'},
        'strategy_number': 20,
    })
    with pytest.raises(ValueError, match='numbered entry identity'):
        seal_first_price_rows((changed,), unit.entry_evidence, unit.base.intents,
                              unit.base.events, unit.first_price_authorities)
