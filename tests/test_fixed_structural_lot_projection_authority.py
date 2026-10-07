from dataclasses import replace
from test_fixed_structural_lot_interval_validator_v2 import ordinal_transport_plan
def selected(monkeypatch):
    from test_fixed_structural_lot_source_v2 import inputs
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    from src.backend import backtest_fixed_structural_lot_source_v4 as source
    from src.backend import backtest_fixed_structural_lot_native_v4 as native
    from src.backend import backtest_fixed_structural_lot_native as owner
    from src.backend import backtest_fixed_structural_lot_execution_v4 as session
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.fixed_structural_lot_release_v4 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=82))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source, name, getattr(previous, name))
    monkeypatch.setattr(source, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent, _, _, parent_release = declarations()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release, release=numbered_strategy(82), policy=old.policy.payload(), approved_code_commit='a' * 40, approved_code_fingerprint='b' * 64, approval_reference='controlled immutable installation seam')['payload'])
    monkeypatch.setattr(source, 'certify_numbered_configuration', lambda *a: parent)
    monkeypatch.setattr(native, 'load_installed_configuration', lambda *a, **kw: (own, old.policy, 'e' * 64))
    monkeypatch.setattr(owner, 'verify_current_installed_source', lambda own: None)
    monkeypatch.setattr(execution, 'prepare_strategy_one_entry_authorities', lambda **kw: (plans.candidates, authority.entry_activity_source.gate, None, None, None, (), authority))

    class Client:

        def close(self):
            calls.append(('closed',))
    actual = session.prepare_fixed_structural_lot_session(plans=plans, number=82, run_id=old.run_id, session_date=old.session_date, market=plans.market, candidates=plans.candidates, entry=plans.entry, seeds=plans.seeds, through_boundary_ms=57600000, client_factory=Client)
    actual.require(market=plans.market, candidates=plans.candidates, entry=plans.entry, through_boundary_ms=57600000, run_id=old.run_id, number=82)
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
    original = replace(_proposal(), strategy_number=18, boundary_ms=41000, target_level_id='R3', bos_support_level_id='R3', momentum=authority.plan.momentum.lookup('AAA', 41000), initial_momentum=authority.plan.source.parent.selection_witness('AAA', 41000))
    proposal = bind_episode_activity_proposal(authority, bind_certified_price_break_proposal(authority.plan, original, strategy_number=36), session_date=old.session_date)
    request = actual.operation.request(proposal)
    request.verify()
    return actual, request, plans


import asyncio
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
import pytest
from src.backend import backtest_fixed_structural_lot_projection_authority as authority
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode, typed_run_config_payload
from src.trading_runtime.journal_contract import canonical_json


def published(monkeypatch, *, publish=True):
    actual, request, plans = selected(monkeypatch)
    source = actual.operation.source
    config = RunConfig(mode=RunMode.BACKTEST, strategy_id=request.strategy_id,
        strategy_revision=request.revision, account_ids=(request.entry.proposal.account_id,),
        anchor_date=source.session_date, run_id=source.run_id, write_progress_checkpoints=False)
    from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
    from src.backend.backtest_v4_run_context import fixed_v4_context_rows
    from src.trading_runtime.arte_journal_writer import publish_typed_run, publish_typed_run_context
    client = ExactDecisionTransport()
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from tests.test_arte_typed_insert_dispatch import Keeper
    client.typed_insert_dispatch = TypedInsertDispatch(Keeper())
    client.typed_insert_strict = True
    if publish:
        client.typed_insert_dispatch.initialize_new_run(source.run_id)
    run, flat = fixed_v4_context_rows(config, execution_interval='100ms',
        configuration_hash=sha256(canonical_json(source.installed_payload).encode()).hexdigest(),
        code_hash='b'*64, market_plan_token=plans.market.token, started_at=request.intent.event_time)
    # Real typed rows/hashes/context fence in explicit in-memory transport.
    if publish:
        publish_typed_run(client, run)
        publish_typed_run_context(client, run_id=source.run_id, config=flat, account_ids=config.account_ids)
    return actual, request, plans, config, client, flat


def test_actual_runtime_start_and_selected_entry_share_distinct_issued_authorities(monkeypatch):
    async def run():
        actual, request, plans, config, client, flat = published(monkeypatch)
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.domain import TradingMode
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from tests.test_arte_journal_commit_v4 import attached_v4_client
        journal = BacktestMemoryJournal(run_id=config.run_id)
        broker = SimulatedBrokerAdapter(config.account_ids, SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST, initial_time=request.intent.event_time)
        strategy = SimpleNamespace(strategy_id=config.strategy_id, revision=config.strategy_revision, automatic=True)
        runtime = TradingRuntime(config, broker, strategy, journal)
        actual.bind_runtime(runtime)
        await runtime.initialize()
        records = journal.unfenced_records()
        start = next(r for r in records if r.category == 'lifecycle')
        assert start.payload['config'] == flat
        context = writer_module.load_typed_run_context(client,config.run_id)
        assert type(context['safety_supervisor_enabled']) is int
        assert type(flat['safety_supervisor_enabled']) is bool
        assert authority.runtime_config_from_context(context) == flat
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
        # Actual selected profile remains issued; transport permission audit is isolated.
        client.fixed_structural_lot_profile = actual.profile
        writer = writer_module.ArteJournalWriter(client,run_id=config.run_id,
            journal_profile='backtest_v4',coalesce_batches=False)
        publisher = BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),
            run_month=actual.operation.source.session_date.replace(day=1),expected_config=flat)
        try:
            actual.operation.bind_publisher(publisher)
            journal.append_fixed_structural_lot_entry(request=request)
            receipt = await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
            assert receipt.last_sequence == journal.latest_sequence(config.run_id)
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            contexts = tuple(client.fixed_structural_lot_contexts)
            cold = ExactCold(client)
            prefix = load_verified_v4_prefix(cold, config.run_id,
                first_price_source=actual.operation.source.price_authority,
                fixed_lot_contexts=contexts, _fixed_lot_cold_source=actual.operation.source,
                _cold_recovery_context_sink=[])
            assert prefix.last_sequence == receipt.last_sequence
            assert publisher._fixed_lot_projection_authority is not None
        finally:
            writer.close(); journal.close()
    asyncio.run(run())


class ExactCold:
    """Independent SELECT-only reader over committed in-memory typed tables."""
    def __init__(self,client):
        from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
        self.reader=ExactDecisionTransport(); self.reader.tables=client.tables
        self.reader.fixed_structural_lot_profile=client.fixed_structural_lot_profile
    def execute(self,sql,**kw):
        assert sql.startswith('SELECT')
        return self.reader.execute(sql,**kw)


@pytest.mark.parametrize('mutation',['mode','run','configuration','code','expected','forged','source','parent_hash','selected_hash'])
def test_projection_authority_rejects_foreign_or_forged_binding(monkeypatch,mutation):
    actual,request,plans,config,client,flat=published(monkeypatch)
    source=actual.operation.source
    from src.trading_runtime import arte_journal_writer as writer
    issued=authority.issue_fixed_lot_projection_authority(client,source,flat)
    if mutation in ('mode','run','configuration','code'):
        context=writer.load_typed_run_context(client,source.run_id)
        field={'mode':'mode','run':'run_id','configuration':'configuration_hash','code':'code_hash'}[mutation]
        context[field]='paper' if mutation=='mode' else 'f'*64
        monkeypatch.setattr(writer,'load_typed_run_context',lambda *_a:context)
        with pytest.raises(ValueError):authority.issue_fixed_lot_projection_authority(client,source,flat)
    else:
        selected=replace(issued) if mutation=='forged' else issued
        selected_source=replace(source) if mutation=='source' else source
        expected={**flat,'strategy_revision':81} if mutation=='expected' else flat
        if mutation in ('parent_hash','selected_hash'):
            object.__setattr__(source,'parent_payload_hash' if mutation=='parent_hash' else 'selected_configuration_hash','f'*64)
        with pytest.raises(ValueError):
            authority.require_fixed_lot_projection_authority(selected,source=selected_source,
                run_id=source.run_id,expected_config=expected)


@pytest.mark.parametrize('value',[2,-1,'1',1.0,None])
def test_persisted_boolean_reconstruction_rejects_untyped_values(monkeypatch,value):
    actual,request,plans,config,client,flat=published(monkeypatch)
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    context=load_typed_run_context(client,config.run_id)
    context['write_progress_checkpoints']=value
    with pytest.raises(ValueError):authority.runtime_config_from_context(context)


def test_successor_seal_remains_unapproved(tmp_path):
    import ast
    import runpy
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v4 as actual
    tree = ast.parse(Path(actual.__file__).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id in {
                'REVIEWED_SOURCE_AST', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'}:
            node.value = ast.parse('{}' if node.targets[0].id == 'REVIEWED_SOURCE_AST'
                else "''", mode='eval').body
    copied = tmp_path / 'src/backend' / Path(actual.__file__).name
    copied.parent.mkdir(parents=True)
    copied.write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    certify = runpy.run_path(str(copied))['certify_fixed_structural_lot_source']
    with pytest.raises(ValueError, match='unapproved'):
        certify()


def test_successor_selected_owner_import_inventory_is_complete():
    import ast
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v4 as certifier
    root = Path(certifier.__file__).resolve().parents[2]
    required = set(certifier.REQUIRED_SOURCE_FILES)
    missing = []
    for relative in required:
        if ('fixed_structural_lot' not in relative and 'strategy_eighty_two' not in relative
                and not relative.endswith('independent_lot_stop_amendment.py')):
            continue
        package = relative[:-3].split('/')[:-1]
        for node in ast.walk(ast.parse((root / relative).read_text(encoding='utf-8'))):
            if type(node) is not ast.ImportFrom:
                continue
            base = '.'.join(package[:len(package) - node.level + 1]) if node.level else ''
            module = '.'.join(part for part in (base, node.module) if part)
            modules = [module] if node.module else [module + '.' + alias.name for alias in node.names]
            for name in modules:
                dependency = root / (name.replace('.', '/') + '.py')
                if dependency.is_file():
                    path = dependency.relative_to(root).as_posix()
                    if path not in required and path != 'src/backend/backtest_fixed_structural_lot_certification_v4.py':
                        missing.append((relative, node.lineno, path))
    assert missing == []


def test_actual_bootstrap_publishes_context_and_runtime_start_with_canonical_config(monkeypatch):
    async def run():
        actual,request,plans,config,context_client,flat=published(monkeypatch,publish=False)
        from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
        from src.backend import backtest_fixed_journal_bootstrap as bootstrap
        from src.backend.backtest_v4_run_context import fixed_v4_context_rows
        from src.trading_runtime import arte_journal_writer as writer
        clients=[]
        for _ in range(3):
            client=ExactDecisionTransport();client.tables=context_client.tables
            client.typed_insert_dispatch=context_client.typed_insert_dispatch
            client.typed_insert_strict=True;client.fixed_structural_lot_profile=actual.profile
            clients.append(client)
        read,write,terminal=clients
        # Explicit transport-only preflight seam; no installed source or database claim.
        monkeypatch.setattr(bootstrap,'fixed_backtest_v2_preflight',lambda *_a:None)
        monkeypatch.setattr(bootstrap,'_v4_cold_reader_preflight',lambda *_a:None)
        monkeypatch.setattr(bootstrap,'_v4_preflight',lambda *_a:None)
        monkeypatch.setattr(writer,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer,'journal_permission_preflight',lambda *a,**k:None)
        run,flat=fixed_v4_context_rows(config,execution_interval='100ms',
            configuration_hash=sha256(canonical_json(actual.operation.source.installed_payload).encode()).hexdigest(),
            code_hash='b'*64,market_plan_token=plans.market.token,started_at=request.intent.event_time)
        assembly=bootstrap.publish_and_assemble_fixed_v4_journal(context_client,read,write,terminal,
            run=run,config=flat,account_ids=config.account_ids,attempt_id=str(uuid4()),expected_config=flat,
            fixed_market_parent_plan=plans.market,fixed_market_execution_plan=plans.execution_market,
            expected_market_start=request.intent.event_time,projection_certifier=lambda:'e'*64,
            writer_factory=lambda *a,**kw:writer.ArteJournalWriter(*a,**kw))
        try:
            actual.operation.bind_publisher(assembly.publisher)
            from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
            from src.trading_runtime.domain import TradingMode
            broker=SimulatedBrokerAdapter(config.account_ids,SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=request.intent.event_time)
            runtime=TradingRuntime(config,broker,SimpleNamespace(strategy_id=config.strategy_id,
                revision=config.strategy_revision,automatic=True),assembly.journal)
            actual.bind_runtime(runtime)
            await runtime.initialize()
            assembly.journal.append_fixed_structural_lot_entry(request=request)
            receipt=await assembly.publisher._drain(target_sequence=assembly.journal.latest_sequence(config.run_id))
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            prefix=load_verified_v4_prefix(terminal,config.run_id,
                first_price_source=actual.operation.source.price_authority,
                fixed_lot_contexts=tuple(write.fixed_structural_lot_contexts),
                _fixed_lot_cold_source=actual.operation.source,_cold_recovery_context_sink=[])
            assert prefix.last_sequence==receipt.last_sequence
            # Resume derives the same seven-field authority from cold persisted context.
            recovered=writer.load_typed_run_context(read,config.run_id)
            assert authority.runtime_config_from_context(recovered)==assembly.publisher.expected_config==flat
            restored=authority.issue_fixed_lot_projection_authority(read,actual.operation.source,
                authority.runtime_config_from_context(recovered))
            authority.require_fixed_lot_projection_authority(restored,source=actual.operation.source,
                run_id=config.run_id,expected_config=flat)
            from src.backend.backtest_journal_memory import BacktestMemoryJournal
            from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
            cold_journal=BacktestMemoryJournal(run_id=config.run_id,initial_sequence=prefix.last_sequence)
            cold_publisher=BacktestTypedJournalPublisher(cold_journal,assembly.writer,attempt_id=str(uuid4()),
                run_month=config.anchor_date.replace(day=1),expected_config=authority.runtime_config_from_context(recovered),
                initial_sequence=prefix.last_sequence,prior_batch_id=prefix.batch_ids[-1],source_cursor=prefix.source_cursor)
            try:
                cold_publisher.restore_fixed_structural_lot_source(actual.operation.source,prefix=prefix,
                    contexts=tuple(write.fixed_structural_lot_contexts))
                assert cold_publisher._fixed_lot_projection_authority is not None
                assert request.intent.intent_id in cold_publisher._committed_fixed_lot_units
            finally:
                cold_journal.close()
        finally:
            assembly.writer.close();assembly.journal.close()
    asyncio.run(run())


@pytest.mark.parametrize('number',[77,80,81])
def test_unselected_projection_rule_delegates_identical_session_arguments(monkeypatch,number):
    from src.backend import backtest_fixed_structural_lot_execution_v4 as facade
    from src.backend import backtest_fixed_structural_lot_execution_v3 as previous
    arguments=dict(plans=object(),number=number,run_id=object(),session_date=object(),market=object(),
        candidates=object(),entry=object(),seeds=object(),through_boundary_ms=object(),client_factory=object())
    calls=[];result=object()
    monkeypatch.setattr(previous,'prepare_fixed_structural_lot_session',lambda **kw:calls.append(kw) or result)
    assert facade.prepare_fixed_structural_lot_session(**arguments) is result
    assert calls[0].keys()==arguments.keys()
    assert all(calls[0][key] is value for key,value in arguments.items())


def test_frozen_successor81_sources_and_seals_remain_exact():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for p in (*root.glob('src/backend/backtest_fixed_structural_lot_*_v3.py'),
            root/'src/trading_runtime/fixed_structural_lot_release_v3.py',
            root/'src/trading_runtime/strategy_eighty_one_release.py',
            root/'src/trading_runtime/strategy_eighty_one_contract.py',
            root/'src/trading_runtime/fixed_structural_lot_interval_validator_v2.py'):
        baseline=subprocess.check_output(['git','show','b89318384b2ecb6915f313e9daba788adee758cb:'+p.relative_to(root).as_posix()],cwd=root).decode('utf-8')
        assert p.read_text(encoding='utf-8')==baseline


def test_terminal_uncompleted_boundary_is_not_fabricated():
    from datetime import timedelta
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_journal_projection import backtest_cursor_record_fields
    from datetime import date
    cursor={'session_date':date(2026,8,18),'boundary_ms':100,'sequence':1}
    # before(work) advances wall/actor clock; finish(work) alone earns this cursor.
    with pytest.raises(ValueError,match='completed boundary'):
        backtest_cursor_record_fields(cursor,{},completed_at=market_day_boundary(cursor['session_date'],100)+timedelta(milliseconds=100))
    assert cursor['boundary_ms']==100 and cursor['sequence']==1
