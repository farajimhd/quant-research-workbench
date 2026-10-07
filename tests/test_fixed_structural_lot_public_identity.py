"""Strategy87 actual-source factory and actor proof; controlled transports explicit."""
from dataclasses import replace


def test_actual_public_app_revision_selector_and_certifier(monkeypatch):
    """Actual app→configuration service→selector→typed configuration certifier.

    Only immutable SELECT transport and the subsequent market preflight are
    controlled. Registry, selector and configuration certification stay real.
    """
    import json
    import re
    from src.backend import app as app_module
    from src.backend import backtest_market_data
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    from src.backend.backtest_fixed_structural_lot_configuration import compile_registered_fixed_structural_lot_configuration
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes
    fixture = Path(os.environ['FIXED_LOT_REGISTERED_CONFIGURATION_TRANSPORT'])
    assert sha256(fixture.read_bytes()).hexdigest() == '7837eff55b00ee58d05b0c4e072c5e8ff6411b3b0ca298996044f68826b51362'
    captured = json.loads(fixture.read_text(encoding='utf-8'))

    class Reader:
        def __init__(self):
            self.queries = []
            self.envelope = None

        def close(self):
            pass

        def execute(self, query):
            self.queries.append(query)
            assert query.startswith('SELECT ')
            if re.search(r'strategy_number=87\b', query):
                assert self.envelope is not None
                if 'configuration_release' in query:
                    e = self.envelope
                    rows = [dict(release_attempt_id='00000000-0000-0000-0000-000000000087',
                        strategy_id=e['payload']['strategy']['strategy_id'],
                        source_candidate_id=e['source_candidate_id'],source_candidate_hash=e['source_candidate_hash'],
                        payload_hash=e['payload_hash'],node_count=e['node_count'],node_hash=e['node_hash'])]
                else:
                    rows = encode_nodes(self.envelope['payload'])
                return '\n'.join(json.dumps(row) for row in rows)
            assert query in captured['queries'], 'Unexpected query outside captured exact immutable ancestry'
            return captured['queries'][query]

    reader = Reader()
    parent = certify_numbered_configuration(reader, 42)
    assert parent.revision()['revision_id'] == captured['parent_identity']['revision_id']
    reader.envelope = compile_registered_fixed_structural_lot_configuration(parent,number=87,
        approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
        approval_reference='explicit offline configuration transport; no source admission')
    own = certify_numbered_configuration(reader, 87)
    monkeypatch.setattr(backtest_market_data,'readonly_clickhouse_client',lambda **_:reader)
    observed = []
    monkeypatch.setattr(app_module,'backtest_preflight',lambda **kwargs:
        observed.append(kwargs['configuration_revision']) or dict(configuration_revision=kwargs['configuration_revision']))
    for number, revision in ((42,parent.revision()),(86,captured['published86_identity']),(87,own.revision())):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',
            session_count=1,initial_cash=10000,configuration_revision_id=revision['revision_id'])
        result = app_module._trading_historical_preflight_payload(request)
        assert result['configuration_revision']['revision_id'] == revision['revision_id']
        assert result['configuration_revision']['revision'] == number
    before = len(reader.queries)
    for identity in ('strategy-one-43:'+own.attempt_id,'strategy-one-44:'+own.attempt_id,
                     'strategy-one-45:'+own.attempt_id,'strategy-one-99999:'+own.attempt_id,
                     'strategy-one-087:'+own.attempt_id,'strategy-one-87:'+'-'*36,
                     'candidate-87'):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',
            configuration_revision_id=identity)
        with pytest.raises(app_module.HTTPException,match='Unknown immutable'):
            app_module._trading_historical_preflight_payload(request)
    assert len(reader.queries) == before
    for extra in (dict(configuration_revision_id='strategy-one-87:00000000-0000-0000-0000-000000000088'),
                  dict(configuration_revision_id=own.revision()['revision_id'],run_plan_id='foreign')):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',**extra)
        with pytest.raises(app_module.HTTPException,match='immutable release'):
            app_module._trading_historical_preflight_payload(request)
    assert len(observed) == 3


from test_fixed_structural_lot_interval_validator_v2 import ordinal_transport_plan


def selected(monkeypatch, *, actual_loader=False):
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    from test_fixed_structural_lot_source_v2 import inputs
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    from src.backend import backtest_fixed_structural_lot_source_v9 as source
    from src.backend import backtest_fixed_structural_lot_source_v5 as scope_owner
    from src.backend import backtest_fixed_structural_lot_native_v9 as native
    from src.backend import backtest_fixed_structural_lot_native as owner
    from src.backend import backtest_fixed_structural_lot_execution_v9 as session
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.fixed_structural_lot_release_v9 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=87))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source if name == '_load_quotes' else scope_owner, name, getattr(previous, name))
    monkeypatch.setattr(scope_owner, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent, _, _, parent_release = declarations()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release, release=numbered_strategy(87), policy=old.policy.payload(), approved_code_commit=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(), approved_code_fingerprint=backend_source_fingerprint(), approval_reference='controlled immutable installation seam')['payload'])
    monkeypatch.setattr(source, 'certify_numbered_configuration', lambda *a: parent)
    if actual_loader:
        monkeypatch.setattr(native, 'certify_numbered_configuration', lambda *a: own)
    else:
        monkeypatch.setattr(native, 'load_installed_configuration', lambda *a, **kw: (own, old.policy, 'e' * 64))
        monkeypatch.setattr(owner, 'verify_current_installed_source', lambda own: None)
    monkeypatch.setattr(execution, 'prepare_strategy_one_entry_authorities', lambda **kw: (plans.candidates, authority.entry_activity_source.gate, None, None, None, (), authority))

    class Client:

        def close(self):
            calls.append(('closed',))
    actual = session.prepare_fixed_structural_lot_session(plans=plans, number=87, run_id=old.run_id, session_date=old.session_date, market=plans.market, candidates=plans.candidates, entry=plans.entry, seeds=plans.seeds, through_boundary_ms=57600000, client_factory=Client)
    actual.require(market=plans.market, candidates=plans.candidates, entry=plans.entry, through_boundary_ms=57600000, run_id=old.run_id, number=87)
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
    original = replace(_proposal(), strategy_number=18, boundary_ms=41000, target_level_id='R3', bos_support_level_id='R3', momentum=authority.plan.momentum.lookup('AAA', 41000), initial_momentum=authority.plan.source.parent.selection_witness('AAA', 41000))
    proposal = bind_episode_activity_proposal(authority, bind_certified_price_break_proposal(authority.plan, original, strategy_number=36), session_date=old.session_date)
    request = actual.operation.request(proposal)
    request.verify()
    return actual, request, plans


import asyncio


from pathlib import Path


import subprocess


import os


from hashlib import sha256


from types import SimpleNamespace


from uuid import uuid4


import pytest


from src.backend import backtest_fixed_structural_lot_projection_runtime_authority as authority


from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode, typed_run_config_payload


from src.trading_runtime.journal_contract import canonical_json


def _actual_native_factory_declaration(monkeypatch, number):
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_native_v9 as native
    from src.trading_runtime.fixed_structural_lot_release_v9 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    parent, _, _, parent_release = declarations()
    commit = os.environ.get('FIXED_LOT_PROPOSED_HEAD') or subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=Path(native.__file__).resolve().parents[2]).decode().strip()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release,
        release=numbered_strategy(number), policy=actual_policy_payload(), approved_code_commit=commit,
        approved_code_fingerprint=authority.backend_source_fingerprint(),
        approval_reference='explicit controlled immutable configuration read seam')['payload'])
    monkeypatch.setattr(native, 'certify_numbered_configuration', lambda *_: own)
    return native, parent, own


def actual_policy_payload():
    from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
    return FixedStructuralLotPolicy().payload()


def test_actual_native_installation_calls_real_source_certifier_and_source_preflight(monkeypatch):
    """Full native load, actual certifier and unmodified clean-source verifier.

    Proposed snapshots explicitly isolate Git identity; post-commit runs use
    actual Git HEAD/status. Configuration SELECT decoding alone is controlled.
    """
    from src.backend import backtest_fixed_structural_lot_certification_v9 as seal
    native, parent, own = _actual_native_factory_declaration(monkeypatch, 87)
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit = os.environ['FIXED_LOT_PROPOSED_HEAD']
        real = subprocess.check_output
        monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
            (commit + '\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else real(args, **kwargs))
    if not seal.REVIEWED_SOURCE_AST:
        with pytest.raises(ValueError, match='unapproved'):
            native.load_installed_configuration(object(), number=87, parent=parent)
        return
    loaded, policy, proof = native.load_installed_configuration(object(), number=87, parent=parent)
    assert loaded is own and policy.payload() == actual_policy_payload()
    assert len(proof) == 64 and all(c in '0123456789abcdef' for c in proof)
    # No certification function, installed loader, or current-source verifier is replaced.
    assert native.verify_current_installed_source.__module__ == 'src.backend.backtest_fixed_structural_lot_native_v5'


def test_actual_native_factory_reaches_runtime_start_and_projection_without_certificate_stubs(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_certification_v9 as seal
    if not seal.REVIEWED_SOURCE_AST:
        pytest.skip('Live source seal is unapproved; positive executes on proposed snapshot and after root approval')
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit = os.environ['FIXED_LOT_PROPOSED_HEAD']
        real = subprocess.check_output
        monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
            (commit + '\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else real(args, **kwargs))
    actual, request, plans, config, client, flat = published(monkeypatch, actual_loader=True)
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
    from src.trading_runtime.domain import TradingMode
    journal = BacktestMemoryJournal(run_id=config.run_id)
    broker = SimulatedBrokerAdapter(config.account_ids, SimulationConfig(initial_cash=10000.),
        mode=TradingMode.BACKTEST, initial_time=request.intent.event_time)
    runtime = TradingRuntime(config, broker, SimpleNamespace(strategy_id=config.strategy_id,
        revision=config.strategy_revision, automatic=True), journal)
    try:
        actual.bind_runtime(runtime)
        asyncio.run(runtime.initialize())
        start = next(row for row in journal.unfenced_records() if row.category == 'lifecycle')
        assert start.payload['config'] == flat
        issued = authority.issue_fixed_lot_projection_authority(client, actual.operation.source, flat)
        authority.require_fixed_lot_projection_authority(issued, source=actual.operation.source,
            run_id=config.run_id, expected_config=flat)
        evidence_path = os.environ.get('FIXED_LOT_NATIVE_EVIDENCE_PATH')
        if evidence_path:
            import json
            from src.backend import backtest_fixed_structural_lot_native as owner
            target = Path(evidence_path).resolve()
            runtime_root = Path('D:/TradingML/runtimes/strategy-optimization-20261005').resolve()
            assert runtime_root in target.parents
            with owner._INSTALLED_LOCK:
                source_proof = owner._INSTALLED_SOURCES[actual.operation.source][-1]
            payload = actual.operation.source.installed_payload
            receipt = dict(schema='fixed-lot-actual-native-factory-start-evidence@1',
                number=87, actual_factory_module='src.backend.backtest_fixed_structural_lot_native_v9',
                actual_certifier_module='src.backend.backtest_fixed_v4_certification',
                actual_certifier_function='certify_numbered_fixed_v4_projection',
                actual_composed_source_proof=source_proof, certifier_mocked=False,
                installed_loader_mocked=False, current_source_verifier_mocked=False,
                configuration_read='explicit controlled complete certificate fixture',
                git_identity_mode='explicit proposed identity seam' if os.environ.get('FIXED_LOT_PROPOSED_HEAD') else 'actual clean committed HEAD/status',
                source_commit=payload['strategy']['numbered_release']['approved_code_commit'],
                backend_fingerprint=payload['strategy']['numbered_release']['approved_code_fingerprint'],
                backtest_runtime_hash=authority.backtest_code_hash(Path(authority.__file__).resolve().parents[2]),
                required_count=len(seal.REQUIRED_SOURCE_FILES), metadata_anchor=seal.APPROVED_METADATA_ANCHOR,
                runtime_start_config_exact=True, published_projection_issued=True,
                source_approval='offline proposal only' if os.environ.get('FIXED_LOT_PROPOSED_HEAD') else 'root approved live seal')
            with target.open('x', encoding='utf-8') as stream:
                json.dump(receipt, stream, indent=2); stream.write('\n')
    finally:
        journal.close()


def unresolved_source_imports(root, relative, source):
    """Absent src files/packages are errors rather than silently ignored paths."""
    import ast
    package = relative[:-3].split('/')[:-1]
    missing = []
    for node in ast.walk(ast.parse(source)):
        if type(node) is ast.ImportFrom:
            base = '.'.join(package[:len(package) - node.level + 1]) if node.level else ''
            module = '.'.join(part for part in (base, node.module) if part)
            modules = [module] if node.module else [module + '.' + alias.name for alias in node.names]
        elif type(node) is ast.Import:
            modules = [alias.name for alias in node.names]
        else:
            continue
        for module in modules:
            if module == 'src' or module.startswith('src.'):
                path = root / module.replace('.', '/')
                if not path.with_suffix('.py').is_file() and not (path / '__init__.py').is_file():
                    missing.append((relative, node.lineno, module))
    return missing


def test_actual_active_native_owner_imports_resolve_and_missing_future_import_rejects():
    from src.backend import backtest_fixed_structural_lot_native_v9 as native
    root = Path(native.__file__).resolve().parents[2]
    active = ('src/backend/backtest_fixed_structural_lot_native_v9.py',
        'src/backend/backtest_fixed_structural_lot_source_v9.py',
        'src/backend/backtest_fixed_structural_lot_execution_v9.py',
        'src/backend/backtest_fixed_structural_lot_empty_v9.py',
        'src/trading_runtime/fixed_structural_lot_release_v9.py',
        'src/trading_runtime/strategy_eighty_seven_release.py',
        'src/trading_runtime/strategy_eighty_seven_contract.py',
        'src/trading_runtime/fixed_structural_lot_causal_clock.py')
    for relative in active:
        source = (root / relative).read_text(encoding='utf-8')
        assert unresolved_source_imports(root, relative, source) == []
        injected = source + '\nfrom src.backend.future_unresolved_native_certificate import issue\n'
        assert unresolved_source_imports(root, relative, injected)
    relative = 'src/backend/backtest_fixed_structural_lot_native_v5.py'
    retained = unresolved_source_imports(root, relative, (root / relative).read_text(encoding='utf-8'))
    assert len(retained) == 1 and retained[0][2] == 'src.backend.backtest_fixed_v5_certification'


def published(monkeypatch, *, publish=True, actual_loader=False):
    actual, request, plans = selected(monkeypatch, actual_loader=actual_loader)
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
        code_hash=authority.backtest_code_hash(Path(authority.__file__).resolve().parents[2]), market_plan_token=plans.market.token, started_at=request.intent.event_time)
    # Real typed rows/hashes/context fence in explicit in-memory transport.
    if publish:
        publish_typed_run(client, run)
        publish_typed_run_context(client, run_id=source.run_id, config=flat, account_ids=config.account_ids)
    return actual, request, plans, config, client, flat


@pytest.mark.parametrize('quote_offset_us',[25515,0])
def test_actual_installed_fractional_fill_roster_first_held_and_cold_snapshot(monkeypatch,quote_offset_us):
    from src.backend import backtest_fixed_structural_lot_certification_v9 as seal
    if not seal.REVIEWED_SOURCE_AST:
        pytest.skip('Actual full source certification positive runs on reviewed proposal and clean committed source')
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit=os.environ['FIXED_LOT_PROPOSED_HEAD'];real=subprocess.check_output
        monkeypatch.setattr(subprocess,'check_output',lambda args,**kw:
            (commit+'\n').encode() if args==['git','rev-parse','HEAD'] else
            b'' if args==['git','status','--porcelain'] else real(args,**kw))
    async def exercise():
        from datetime import timedelta
        from time import perf_counter
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
        from src.trading_runtime.domain import TradingMode,InstrumentContract
        from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
        from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy
        from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from src.trading_runtime.fixed_structural_lot_snapshot import project_fixed_structural_lot_snapshot,restore_fixed_structural_lot_snapshot
        from src.backend.backtest_market_data import market_day_boundary
        actual,request,plans,config,client,flat=published(monkeypatch,actual_loader=True)
        entry=request.entry.proposal;at=request.intent.event_time
        journal=BacktestMemoryJournal(run_id=config.run_id)
        broker=SimulatedBrokerAdapter(config.account_ids,SimulationConfig(initial_cash=10000.),mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
        profile=PortfolioAccountProfile('cash',entry.account_id,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        portfolio=PortfolioManagementEngine((profile,),journal=journal,run_id=config.run_id,strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,event_clock=lambda:at)
        planner=RuntimeIbkrStrategyOrderPlanner({entry.ticker:InstrumentContract(entry.ticker,1,entry.ticker,'STK','USD')},strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,run_id=config.run_id)
        runtime=TradingRuntime(config,broker,SimpleNamespace(strategy_id=config.strategy_id,revision=config.strategy_revision,automatic=True),journal,portfolio=portfolio,intent_planner=planner)
        actual.bind_runtime(runtime)
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
        client.fixed_structural_lot_profile=actual.profile
        writer=writer_module.ArteJournalWriter(client,run_id=config.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),run_month=actual.operation.source.session_date.replace(day=1),expected_config=flat)
        try:
            await runtime.initialize();actual.operation.bind_publisher(publisher)
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot(entry.ticker,10.,10.01,.01,at,'arte.liquidity_100ms_v1'))
            initial_quote=request.intent.event_time-timedelta(microseconds=1000)
            local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            initial_us=int(initial_quote.timestamp()*1000000)
            initial_row=dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,event_count=1,first_event_us=initial_us,last_event_us=initial_us,quote_timestamp_us=initial_us,quote_valid=1,bid_int=100000,ask_int=100100,bid_size=10000.,ask_size=10000.,price_valid=1,close_int=100100,extremes_valid=1,low_int=100100,high_int=100100,execution_volume=100000.)
            # Actual recorded failure offsets: entry +25.515ms quote, +100ms fill.
            now_ms=entry.boundary_ms+100;at=market_day_boundary(actual.operation.source.session_date,now_ms)
            quote_time=request.intent.event_time+timedelta(microseconds=quote_offset_us)
            quote_us=int(quote_time.timestamp()*1000000);end_us=int(at.timestamp()*1000000)
            local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            row=dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,event_count=1,first_event_us=quote_us,last_event_us=quote_us,quote_timestamp_us=quote_us,quote_valid=1,bid_int=100000,ask_int=100100,bid_size=10000.,ask_size=10000.,price_valid=1,close_int=100100,extremes_valid=1,low_int=100100,high_int=100100,execution_volume=4.,execution_price_levels=({'price_int':100100,'volume':4.},))
            row['ask_size']=4.
            from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler,run_strategy_one_boundaries
            from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
            from src.backend.backtest_strategy_one_preparation import iter_strategy_one_entries
            initial_row.update(session_date=actual.operation.source.session_date,boundary_ms=entry.boundary_ms,indicator_resolution_ms=100)
            row.update(session_date=actual.operation.source.session_date,boundary_ms=now_ms,indicator_resolution_ms=100)
            cursor=next(cursor for cursor in iter_strategy_one_entries(plans.candidates.prepared) if cursor.ticker==entry.ticker and cursor.boundary_ms==entry.boundary_ms)
            candidates=(StrategyOneDecisionCandidate(initial_row,cursor),)
            scheduler=StrategyOneBoundaryScheduler(session_date=actual.operation.source.session_date,candidate_rows=iter(candidates),
                active_source=lambda ticker,after:iter(((now_ms,{100:row}),) if ticker==entry.ticker and after<now_ms else ()))
            reached=[]
            class BoundedHeldBoundaryReached(Exception):pass
            async def process(work):
                reached.append(work.boundary_ms)
                await runtime.process_liquidity_boundary([resolutions[100] for _,resolutions in work.broker_rows],
                    at=market_day_boundary(actual.operation.source.session_date,work.boundary_ms))
            async def evaluate(ticker,resolutions,candidate):
                if candidate is not None:
                    assert candidate.evidence.boundary_ms==entry.boundary_ms
                    results=await runtime.submit_fixed_structural_lot_request(request)
                    assert len(results)==1 and results[0]['decision']['status'] in ('approved','resized')
            async def finish(work):
                if work.boundary_ms==now_ms:raise BoundedHeldBoundaryReached()
            with pytest.raises(BoundedHeldBoundaryReached):
                await run_strategy_one_boundaries(scheduler,process_broker_boundary=process,evaluate_ticker=evaluate,
                    financially_active_tickers=broker.financially_active_tickers,finish_boundary=finish)
            assert reached==[entry.boundary_ms,now_ms]
            group=next(iter(runtime.order_manager._groups.values()))
            assert group.filled_quantity>0 and group.updated_at==quote_time
            from src.trading_runtime.arte_oms_projection import canonical_oms_order_metadata
            from src.trading_runtime.independent_lot_initial_stop_lineage import initial_metadata
            from src.trading_runtime.strategy_orders import canonical_runtime_metadata
            planned=next(v for v in journal.unfenced_records() if v.entity_type=='order_group_state' and v.payload.get('event')=='ladder_repair_planned')
            frozen=journal.oms_group_for_record(planned.record_id)
            acknowledged=journal.oms_effective_protection_for_record(planned)
            repair=next(v for i,v in enumerate(frozen.orders) if i not in frozen.broker_order_request_indexes.values() and v.side=='SELL')
            check=dict(source=actual.operation.source,run_id=config.run_id,strategy_id=config.strategy_id,
                strategy_revision=config.strategy_revision,sequence=planned.sequence,boundary=planned.event_time)
            metadata=canonical_runtime_metadata(repair,frozen.intent)
            assert 'confirmed_support_stop' not in metadata
            assert initial_metadata(frozen,repair,metadata,acknowledged,**check)==metadata
            # An inactive original bracket still has a real effective price ACK;
            # no active capacity, binding or repair acknowledgement is invented.
            inactive={k:replace(v,payload={**v.payload,'active':False}) for k,v in acknowledged.items()}
            assert initial_metadata(frozen,repair,metadata,inactive,**check)==metadata
            selected=next(v for v in acknowledged.values() if v.payload.get('kind')=='stop'
                and frozen.plan.order_slice_ids[frozen.broker_order_request_indexes[v.payload['order_id']]]==frozen.plan.order_slice_ids[frozen.orders.index(repair)])
            for change in ('account','run','strategy','ticker','group','intent','broker','sequence','time','price'):
                bad=replace(selected,account_id='foreign') if change=='account' else replace(selected,run_id=str(uuid4())) if change=='run' else replace(selected,sequence=planned.sequence) if change=='sequence' else replace(selected,event_time=planned.event_time+timedelta(microseconds=1)) if change=='time' else replace(selected,payload={**selected.payload,
                    {'strategy':'strategy_revision','ticker':'ticker','group':'order_group_id','intent':'source_intent_id','broker':'order_id','price':'price'}[change]:9.9 if change=='price' else -1 if change=='strategy' else 'foreign'})
                invalid={k:bad if v.sequence==selected.sequence else v for k,v in acknowledged.items()}
                with pytest.raises(ValueError):initial_metadata(frozen,repair,metadata,invalid,**check)
            import copy
            mutated=copy.deepcopy(frozen)
            mutated.intent.metadata['confirmed_support_stop']=9.9
            with pytest.raises(ValueError):
                canonical_oms_order_metadata(mutated,repair,acknowledged,source_sequence=planned.sequence,
                    source_boundary=planned.event_time,source_run_id=config.run_id,fixed_lot_source=actual.operation.source,
                    source_strategy_id=config.strategy_id,source_strategy_revision=config.strategy_revision)
            await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
            owner=NativeFixedStructuralLotManagement(operation=actual.operation,publisher=publisher,client=client)
            prefix,contexts=owner._prefix();args=owner._arguments(request,prefix,contexts)
            from src.trading_runtime.arte_oms_projection import (load_committed_oms_group_state_page,
                load_committed_oms_admission_page,load_committed_oms_decision_page,reconstruct_strategy_one_oms_lineage)
            from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
            from src.trading_runtime.arte_intent_projection import RecoveredIntent
            pending=next(v for v in load_committed_oms_group_state_page(client,prefix,limit=500,fixed_lot_contexts=contexts)
                if v.sequence==planned.sequence)
            cold_history=load_complete_typed_protection_history(client,prefix,fixed_lot_contexts=contexts)
            admissions=load_committed_oms_admission_page(client,prefix,(pending,))
            decisions=load_committed_oms_decision_page(client,prefix,(pending,),admissions)
            own_context=next(v for v in contexts if v.record.entity_id==request.intent.intent_id)
            cold_intent=RecoveredIntent(own_context.record.sequence,own_context.record.account_id,own_context.record.record_id,
                own_context.base.batch_id,request.intent,own_context.base)
            cold_orders=reconstruct_strategy_one_oms_lineage(pending,cold_intent,cold_history,
                admission_reservation=admissions[pending.sequence],admission_decision=decisions[pending.sequence],
                fixed_lot_source=actual.operation.source)
            assert cold_orders==frozen.orders
            if quote_offset_us:
                import ast
                from src.trading_runtime import fixed_structural_lot_management as current_reader
                frozen_path=os.environ.get('FIXED_LOT_FROZEN_ROSTER_SOURCE')
                frozen_source=Path(frozen_path).read_text(encoding='utf-8') if frozen_path else subprocess.check_output(['git','show','aeacd397325a4b2aabae68634fb90b35440dc6cc:src/trading_runtime/fixed_structural_lot_management.py']).decode('utf-8')
                old_function=next(node for node in ast.parse(frozen_source).body if isinstance(node,ast.FunctionDef) and node.name=='load_fixed_structural_lot_stop_ceiling')
                namespace=dict(vars(current_reader))
                exec(compile(ast.Module(body=[old_function],type_ignores=[]),'frozen84_actual_roster_reader','exec'),namespace)
                with pytest.raises(ValueError,match='precedes its entry'):
                    namespace['load_fixed_structural_lot_stop_ceiling'](**args,entry=request.entry,group_id=group.group_id)
            owner.register_entry(request,group.group_id)
            with pytest.raises(ValueError,match='exceeds'):
                await owner.first_held((entry.account_id,entry.assignment_id,entry.ticker),boundary_ms=entry.boundary_ms)
            started=perf_counter();state=await owner.first_held((entry.account_id,entry.assignment_id,entry.ticker),boundary_ms=now_ms);elapsed=perf_counter()-started
            assert state.protection.boundary_ms==now_ms and now_ms%100==0
            assert state.roster.observed_boundary_ms==now_ms
            prefix,contexts=owner._prefix();args=owner._arguments(request,prefix,contexts)
            rows=project_fixed_structural_lot_snapshot(state,**args)
            restored=restore_fixed_structural_lot_snapshot(rows,entry=request.entry,**args)
            assert restored==state
            print('actual_native_first_held_seconds='+str(round(elapsed,6)))
            # Continue the same actual coordinator with a later partial-fill
            # completion and an owned target/stop execution. Quotes remain stale;
            # committed execution envelopes, not OMS updated_at, earn each clock.
            from src.trading_runtime.fixed_structural_lot_state import _fresh,open_fixed_structural_lot_protection
            from src.trading_runtime.fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
            from src.trading_runtime.fixed_structural_lot_causal_clock import committed_observed_clock
            from src.trading_runtime.arte_oms_projection import load_latest_committed_oms_groups
            assert state.roster.acquiring
            from decimal import Decimal
            owned_target=min(v.price for v in group.orders if v.side=='SELL' and v.orderType=='LMT')
            target_int=int(Decimal(str(owned_target))*10000)+100
            continuation=[]
            cash_after_acquisition=[]
            quantity_after_acquisition=[]
            for offset in ((200,300) if quote_offset_us else (200,300,400)):
                boundary=entry.boundary_ms+offset
                price=100100 if offset==200 else (target_int if quote_offset_us else 97900)
                trade_us=int(market_day_boundary(actual.operation.source.session_date,boundary).timestamp()*1000000)-1000
                later=dict(row,first_event_us=trade_us,last_event_us=trade_us,boundary_ms=boundary,bucket_index=row['bucket_index']+offset//100-1,
                    execution_volume=100000.,execution_price_levels=({'price_int':price,'volume':100000.},),ask_size=10000.,close_int=price,low_int=price,high_int=price,
                    bid_int=100000,ask_int=100100)
                if quote_offset_us and offset>=300:
                    later.update(quote_timestamp_us=trade_us,bid_int=price-100,ask_int=price)
                continuation.append((boundary,{100:later}))
            clock=StrategyOneBoundaryScheduler(session_date=actual.operation.source.session_date,candidate_rows=iter(()),
                active_source=lambda ticker,after:iter(v for v in continuation if v[0]>after),start_after_boundary_ms=now_ms)
            async def later_evaluate(ticker,resolutions,candidate):pass
            async def later_finish(work):
                await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
                later_prefix,later_contexts=owner._prefix();later_args=owner._arguments(request,later_prefix,later_contexts)
                if work.boundary_ms==entry.boundary_ms+200:
                    fresh=_fresh(state,now_ms=work.boundary_ms,**later_args)
                    assert not fresh.acquiring and fresh.observed_boundary_ms==work.boundary_ms
                    cash_after_acquisition.append(broker.checkpoint_state()['cash'][entry.account_id])
                    quantity_after_acquisition.append(sum(v['quantity'] for v in broker.checkpoint_state()['positions'][entry.account_id]))
                    with pytest.raises(ValueError,match='future'):_fresh(state,now_ms=now_ms,**later_args)
                    with pytest.raises(ValueError):restore_fixed_structural_lot_snapshot(rows,entry=request.entry,**later_args)
                    reopened=open_fixed_structural_lot_protection(request.entry,group_id=group.group_id,now_ms=work.boundary_ms,**later_args)
                    checkpoint=project_fixed_structural_lot_snapshot(reopened,**later_args)
                    assert restore_fixed_structural_lot_snapshot(checkpoint,entry=request.entry,**later_args)==reopened
                    committed=next(v for v in load_latest_committed_oms_groups(client,later_prefix,
                        allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=later_contexts) if v.group['group_id']==group.group_id)
                    sequence=next(v.record.sequence for v in later_contexts if v.record.entity_id==request.intent.intent_id)
                    class OmittedOwnedFill:
                        def execute(self,sql,**kw):
                            result=client.execute(sql,**kw)
                            # Match the actual SQL membership predicates before
                            # exercising the real parent-envelope verifier.
                            for field in ('entity_id','execution_id'):
                                if 'AND '+field+' IN (' in sql:
                                    import json,re
                                    wanted=set(re.findall(r"'([^']+)'",sql.split('AND '+field+' IN (',1)[1].split(')',1)[0]))
                                    result='\n'.join(json.dumps(v) for line in result.splitlines() if line
                                        for v in (json.loads(line),) if v[field] in wanted)
                            if 'FROM arte.trading_execution_v1 ' in sql and 'broker_order_id IN (' in sql:
                                import json
                                values=[json.loads(v) for v in result.splitlines() if v]
                                assert len(values)>1
                                return '\n'.join(json.dumps(v) for v in values[1:])
                            return result
                    with pytest.raises(ValueError,match='complete broker quantities'):
                        committed_observed_clock(OmittedOwnedFill(),later_prefix,committed,session_date=actual.operation.source.session_date,
                            entry_boundary_ms=entry.boundary_ms,entry_sequence=sequence,now_ms=work.boundary_ms)
                    with pytest.raises(ValueError,match='exceeds bound'):
                        committed_observed_clock(client,later_prefix,committed,session_date=actual.operation.source.session_date,
                            entry_boundary_ms=entry.boundary_ms,entry_sequence=sequence,now_ms=work.boundary_ms,max_fills=1)
                elif quote_offset_us or work.boundary_ms==entry.boundary_ms+400:
                    roster=load_fixed_structural_lot_stop_ceiling(entry=request.entry,group_id=group.group_id,now_ms=work.boundary_ms,**later_args)
                    assert roster.observed_boundary_ms==work.boundary_ms
                    sold=[v for v in client.tables['trading_execution_v1'] if v['side'] in ('S','SELL')]
                    assert sold and all(float(v['quantity'])>0 for v in sold)
                    final_broker=broker.checkpoint_state()
                    assert final_broker['cash'][entry.account_id]>cash_after_acquisition[0]
                    assert sum(v['quantity'] for v in final_broker['positions'][entry.account_id])<quantity_after_acquisition[0]
                    committed=next(v for v in load_latest_committed_oms_groups(client,later_prefix,
                        allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=later_contexts) if v.group['group_id']==group.group_id)
                    roles={v['broker_order_id']:v['role'] for v in committed.broker_bindings}
                    assert {roles[v['broker_order_id']] for v in sold}==({'profit_target'} if quote_offset_us else {'protective_stop'})
                    with pytest.raises(ValueError,match='exceeds'):
                        load_fixed_structural_lot_stop_ceiling(entry=request.entry,group_id=group.group_id,now_ms=work.boundary_ms-100,**later_args)
                    raise BoundedHeldBoundaryReached()
            with pytest.raises(BoundedHeldBoundaryReached):
                await run_strategy_one_boundaries(clock,process_broker_boundary=process,evaluate_ticker=later_evaluate,
                    financially_active_tickers=broker.financially_active_tickers,finish_boundary=later_finish)

        finally:
            if runtime.order_manager is not None:await runtime.order_manager.close()
            for task in (runtime._broker_stream_task,runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            writer.close();journal.close()
    asyncio.run(exercise())



def test_release_87_changes_only_declared_warm_proof_and_identity():
    from src.trading_runtime.strategy_registry import numbered_strategy
    from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as RULE
    previous=numbered_strategy(86);current=numbered_strategy(87)
    assert current.input_contracts==previous.input_contracts
    assert current.rule_set_contracts==(*previous.rule_set_contracts,RULE)
    assert current.evaluation_interval==previous.evaluation_interval
    from tests.test_fixed_structural_lot_native import declarations
    from src.trading_runtime.fixed_structural_lot_release_v8 import derive_fixed_structural_lot_release as old
    from src.trading_runtime.fixed_structural_lot_release_v9 import derive_fixed_structural_lot_release as new
    parent,_,_,parent_release=declarations()
    args=dict(parent_release=parent_release,policy=actual_policy_payload(),approved_code_commit='a'*40,approved_code_fingerprint='b'*64,approval_reference='pure tree equivalence only')
    a=old(parent,release=previous,**args)['payload']['strategy']['parameters']
    b=new(parent,release=current,**args)['payload']['strategy']['parameters']
    assert a==b


def test_exact_parent_full_ast_restorations_and_foreign_delta_rejection():
    import ast
    from src.backend.backtest_fixed_structural_lot_compatibility_v9 import REVIEWED_PARENT_DELTAS,restore_reviewed_parent_source
    root=Path(__file__).resolve().parents[1]
    for relative,(current,baseline,_) in REVIEWED_PARENT_DELTAS.items():
        raw=(root/relative).read_text(encoding='utf-8')
        restored=restore_reviewed_parent_source(raw,relative)
        actual=subprocess.check_output(['git','show','cb4850b99b37b326ed0c98c359baa81afd3bf932:'+relative],cwd=root).decode('utf-8')
        assert ast.dump(ast.parse(restored))==ast.dump(ast.parse(actual)),relative
        changed=raw+'\nforeign_unreviewed_source_change = True\n'
        assert restore_reviewed_parent_source(changed,relative)==changed


from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo

def test_actual_87_selected_controller_checkpoint_and_fresh_cold_actors(monkeypatch):
    async def run():
        from tests import test_fixed_structural_lot_empty as fixture
        from tests.test_backtest_strategy_one_candidate_store import _market,THROUGH
        from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan,RULE_DIGEST
        from src.backend.backtest_strategy_one_plan import StrategyOneFixedPlans
        from src.backend.backtest_market_data import project_empty_market_day_plan
        from src.backend import backtest_fixed_structural_lot_native_v9 as native
        from src.backend.backtest_fixed_structural_lot_execution_v9 import prepare_fixed_structural_lot_session
        from src.backend.backtest_fixed_structural_lot_execution import bind_fixed_structural_lot_manager
        native,parent,own=_actual_native_factory_declaration(monkeypatch,87)
        monkeypatch.setattr(native,'certify_numbered_configuration',lambda client,number:parent if number==42 else own)
        import src.backend.backtest_market_data as market_module
        import src.backend.backtest_input_scope as scope_module
        monkeypatch.setattr(market_module,'verify_market_day_plan',lambda *a,**kw:None)
        monkeypatch.setattr(scope_module,'input_exclusions',lambda day:())
        market=replace(_market(),token=sha256(repr(_market()).encode()).hexdigest());reader=fixture.EmptyReader()
        candidates=certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,through_boundary_ms=57_600_000,client=reader)
        plans=StrategyOneFixedPlans(market,None,project_empty_market_day_plan(market,empty_candidate_token=candidates.token),None,candidates,None,None,None,None,None,None)
        def source_client():
            reader=fixture.EmptyReader();reader.close=lambda:None
            return reader
        prepared=prepare_fixed_structural_lot_session(plans=plans,number=87,run_id=str(uuid4()),
            session_date=date.fromisoformat(market.sessions[0]),market=market,candidates=candidates,
            entry=None,seeds=None,through_boundary_ms=THROUGH,client_factory=source_client)
        source=prepared.operation.source
        assert prepared.entry_authorities[1:]==(None,None,None,None,(),None)
        account='DU-EMPTY'
        at=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
            +timedelta(hours=4,milliseconds=source.through_boundary_ms)).astimezone(timezone.utc)
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
        from src.trading_runtime.domain import TradingMode
        from src.trading_runtime.risk import RiskAuthority
        from src.trading_runtime.order_management import OrderManagementEngine
        from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy
        from src.trading_runtime.runtime import TradingRuntime
        from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
        from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
        from tests.test_arte_journal_commit_v4 import attached_v4_client
        from tests.test_arte_journal_writer import run_row,run_context
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.trading_runtime import fixed_structural_lot_profile as profile_module
        from src.trading_runtime.arte_journal_writer import publish_typed_run,publish_typed_run_context
        journal=BacktestMemoryJournal(run_id=source.run_id)
        broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
        await broker.initialize()
        risk=RiskAuthority();await risk.prime(broker,[account])
        profile=PortfolioAccountProfile('cash',account,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        config=dict(mode='backtest',strategy_id=source._strategy_id,strategy_revision=source._revision,
            parent_configuration_hash=source.parent_payload_hash,selected_configuration_hash=source.selected_configuration_hash)
        client=ExactDecisionTransport()
        from src.backend.backtest_v4_run_context import fixed_v4_context_rows
        from src.trading_runtime.runtime import RunConfig,RunMode
        runtime_config=RunConfig(RunMode.BACKTEST,source._strategy_id,source._revision,(account,),source.session_date,run_id=source.run_id,safety_supervisor_enabled=False,write_progress_checkpoints=False)
        run,flat=fixed_v4_context_rows(runtime_config,execution_interval='100ms',configuration_hash=sha256(canonical_json(source.installed_payload).encode()).hexdigest(),code_hash=authority.backtest_code_hash(Path(authority.__file__).resolve().parents[2]),market_plan_token=source.market.token,started_at=at)
        publish_typed_run(client,run)
        publish_typed_run_context(client,run_id=source.run_id,config=flat,account_ids=(account,))
        config=flat
        client=attached_v4_client(client)
        client.fixed_structural_lot_profile=prepared.profile
        # Only uninstalled infrastructure is controlled. No actor, financial
        # capture, full V4 prefix, selected projection or cold decoder is stubbed.
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'_verify_run_identity',lambda *a:dict(mode='backtest',account_ids=(account,)))
        writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),
            run_month=source.session_date.replace(day=1),expected_config=config)
        publisher.bind_fixed_structural_lot_source(source)
        portfolio=PortfolioManagementEngine((profile,),journal=journal,run_id=source.run_id,
            strategy_id=source._strategy_id,strategy_revision=source._revision,event_clock=lambda:at)
        await portfolio.synchronize(broker)
        def no_plan(*args,**kwargs):raise AssertionError('Empty horizon must not create an order')
        from src.trading_runtime.runtime import RunConfig,RunMode
        runtime=TradingRuntime(RunConfig(RunMode.BACKTEST,source._strategy_id,source._revision,
            (account,),source.session_date,run_id=source.run_id,safety_supervisor_enabled=False,
            write_progress_checkpoints=False),broker,
            SimpleNamespace(strategy_id=source._strategy_id,revision=source._revision,
                automatic=True,assignments=lambda:()),journal,risk=risk,
            portfolio=portfolio,intent_planner=SimpleNamespace(plan=no_plan))
        oms=runtime.order_manager
        runtime.last_event_time=at
        prepared.bind_runtime(runtime)
        class NoEvidence:
            def capture_recovery_state(self):
                from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
                return StrategyOneEvidenceState(source.through_boundary_ms,(),(),())
            async def management_evidence(self,*a,**k):raise AssertionError('Empty horizon needs no price evidence')
        manager=StrategyOneManagementRunner(runtime=runtime,evidence=NoEvidence(),tick_for_ticker=no_plan)
        owner=bind_fixed_structural_lot_manager(prepared,manager=manager,publisher=publisher,session=source.session_date)
        with pytest.raises(ValueError):prepared.operation.request(object())
        with pytest.raises(ValueError):prepared.bind_runtime(runtime)
        client.fixed_structural_lot_contexts=()
        try:
            
            from src.trading_runtime.keeper_session import ManagedKeeperSession
            from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch,_context_receipt_path
            keeper=journal_lease_keeper();keeper.add_listener=lambda listener:None
            
            session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
            client.manager_keeper_session=session
            dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(source.run_id)
            keeper.create(_context_receipt_path(source.run_id),b'1\n'+b'a'*64)
            client.typed_insert_dispatch=dispatch
            from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
            lease=BacktestV4KeeperLease.acquire(session,run_id=source.run_id,owner_id=str(uuid4()))
            client.backtest_v4_lease=lease
            client.typed_insert_strict=True
            # Native fixed-bar broker starts without fabricated market rows.
            initial=journal.append(run_id=source.run_id,category='checkpoint',entity_type='market_boundary',
                entity_id=f'{source.session_date.isoformat()}:100',event_time=at-timedelta(milliseconds=source.through_boundary_ms-100),
                payload=dict(session_date=source.session_date.isoformat(),boundary_ms=100,market_sequence=0,
                    frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None))
            publisher.enqueue_pending()
            assert (await publisher.await_fence()).last_sequence==initial.sequence
            from src.backend.replay_run_service import ReplayRunController
            controller=object.__new__(ReplayRunController)
            controller.definition=SimpleNamespace(mode=RunMode.BACKTEST,configuration_revision={})
            controller.run_id=source.run_id;controller.status='running'
            controller._journal=journal;controller._journal_publisher=publisher
            controller._runtime=runtime;controller._strategy_one_manager=manager
            controller._account_map={'cash':account};controller.processed_events=0;controller.warmup_events=0
            controller._processed_frames=0;controller._pending_passive_market_events=[]
            controller._source_cursor=dict(session_date=source.session_date.isoformat(),boundary_ms=source.through_boundary_ms,sequence=0)
            controller._frame_cursor={};controller._checkpoint_io_task=None
            controller.stream_snapshot=lambda:dict(status='running')
            controller._record_stage_time=lambda *a:None
            controller._restart_checkpoint_interval_events=lambda:100
            from threading import Event
            from time import monotonic
            dispatch_started=Event();dispatch_release=Event()
            execute=client.execute
            def pending_insert(sql,*,query_id=None):
                header=sql.split(b'\n',1)[0].decode() if isinstance(sql,bytes) else sql.split('\n',1)[0]
                if header.startswith('INSERT INTO arte.trading_event_v1 ') and not dispatch_started.is_set():
                    dispatch_started.set()
                    if not dispatch_release.wait(10):raise AssertionError('Owned pending INSERT was not released')
                return execute(sql,query_id=query_id)
            client.execute=pending_insert
            second=journal.append(run_id=source.run_id,category='checkpoint',entity_type='market_boundary',
                entity_id=f'{source.session_date.isoformat()}:200',event_time=at-timedelta(milliseconds=source.through_boundary_ms-200),
                payload=dict(session_date=source.session_date.isoformat(),boundary_ms=200,market_sequence=0,
                    frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None))
            pending=publisher.enqueue_pending();deadline=monotonic()+5
            try:
                while not dispatch_started.is_set() and monotonic()<deadline:await asyncio.sleep(.002)
                assert dispatch_started.is_set() and not pending.done()
                gate,_=dispatch._read_gate(source.run_id)
                assert gate.inflight>0
                checkpoint=asyncio.create_task(controller._save_restart_checkpoint_responsive(at))
                await asyncio.sleep(.02)
                assert not checkpoint.done() and publisher._task is pending
                print(f'actual_selected_controller_awaits_owned_pending_receipt inflight={gate.inflight} registered={gate.registered}')
            finally:
                dispatch_release.set()
                await publisher.await_fence()
                client.execute=execute
            assert pending.result().last_sequence==second.sequence
            receipt=await checkpoint
            assert receipt.last_sequence==publisher.fenced_sequence
            record=SimpleNamespace(sequence=receipt.last_sequence)
            captured=owner.capture(manager,boundary_ms=source.through_boundary_ms)
            assert receipt.last_sequence==record.sequence
            from src.trading_runtime.fixed_structural_lot_warm_proof import load_prefix,_CACHE
            first=load_prefix(client,source.run_id)
            assert load_prefix(client,source.run_id) is first and client in _CACHE
            assert captured.selected_positions==captured.financials==()
            assert not await broker.live_orders() and not await broker.positions(account)
            ledger=await broker.account_ledger(account)
            assert ledger.cashbalance==ledger.netliquidationvalue==10000.
            assert ledger.realizedpnl==ledger.unrealizedpnl==0.
            assert not portfolio.reservations and not oms._groups
            from src.trading_runtime.fixed_structural_lot_manager_snapshot import load_cold_manager_image,_PUBLICATIONS
            _PUBLICATIONS.clear();owner._checkpoints.clear()
            # The genuine factory must certify the same run identity afresh.
            # Never copy/reseal the issued empty object.
            from src.backend import backtest_fixed_structural_lot_empty_v9 as empty
            reader=fixture.EmptyReader()
            fresh=empty.prepare_empty_fixed_structural_lot_source(reader,number=87,run_id=source.run_id,
                session_date=source.session_date,plans=plans,
                through_boundary_ms=source.through_boundary_ms)
            image=load_cold_manager_image(client,session,source=fresh,fixed_lot_contexts=())
            assert image.selected_positions==() and image.inherited.positions==image.inherited.submitted==()
            assert image.sequence==record.sequence and image.source is fresh
            from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
            from src.trading_runtime.strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
            from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            from src.backend.backtest_fixed_journal_bootstrap import restore_fixed_structural_lot_native_manager
            prefix=load_verified_v4_prefix(client,source.run_id)
            recovered=recover_portfolio_engine_state(client,run_id=source.run_id,profiles=(profile,),
                state_revisions={account:image.sequence},cutoff_at=at)
            cold_broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
            await cold_broker.initialize()
            rows=load_unattested_broker_match_snapshot(client,run_id=source.run_id,checkpoint_sequence=image.sequence)
            cold_broker.restore_checkpoint_state(reconstruct_broker_match_state(rows,requests_by_broker_id={},quotes={}))
            cold_journal=BacktestMemoryJournal(run_id=source.run_id,initial_sequence=prefix.last_sequence)
            cold_writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
            cold_publisher=BacktestTypedJournalPublisher(cold_journal,cold_writer,attempt_id=str(uuid4()),
                run_month=source.session_date.replace(day=1),expected_config=config,initial_sequence=prefix.last_sequence,
                prior_batch_id=prefix.last_batch_id,source_cursor=prefix.source_cursor)
            cold_publisher.restore_fixed_structural_lot_source(fresh,prefix=prefix,contexts=())
            cold_portfolio=PortfolioManagementEngine((profile,),journal=cold_journal,run_id=source.run_id,
                strategy_id=source._strategy_id,strategy_revision=source._revision,typed_recovery=recovered,event_clock=lambda:at)
            cold_risk=RiskAuthority();await cold_risk.prime(cold_broker,[account])
            cold_oms=OrderManagementEngine(broker=cold_broker,planner=no_plan,risk=cold_risk,journal=cold_journal,
                run_id=source.run_id,strategy_id=source._strategy_id,strategy_revision=source._revision,causal_execution_clock=True)
            # Bootstrap receives entirely new actors and source issuance; no
            # previous manager, journal cache or broker checkpoint is delegated.
            cold_runtime=object.__new__(TradingRuntime)
            cold_runtime.config=runtime.config;cold_runtime.run_id=source.run_id;cold_runtime.journal=cold_journal
            cold_runtime.portfolio=cold_portfolio;cold_runtime.broker=cold_broker;cold_runtime.order_manager=cold_oms
            cold_runtime.strategy=SimpleNamespace(assignments=lambda:())
            cold_owner=NativeFixedStructuralLotManagement(operation=NativeFixedStructuralLotOperation(fresh),
                publisher=cold_publisher,client=client)
            cold_manager=StrategyOneManagementRunner(runtime=cold_runtime,evidence=NoEvidence(),tick_for_ticker=no_plan)
            cold_manager.bind_fixed_structural_lot_management(cold_owner)
            try:
                cold_broker._cash[account]=9999.
                with pytest.raises(ValueError,match='complete actual broker checkpoint differs'):
                    await restore_fixed_structural_lot_native_manager(cold_owner,cold_manager,session)
                assert cold_manager._positions==cold_manager._submitted=={}
                cold_broker._cash[account]=10000.
                restored=await restore_fixed_structural_lot_native_manager(cold_owner,cold_manager,session)
                assert restored.inherited==image.inherited
                assert cold_manager._positions==cold_manager._submitted=={}
                assert not cold_owner.states and not cold_portfolio.reservations and not cold_oms._groups
                assert (await cold_broker.account_ledger(account)).cashbalance==10000.
                assert not await cold_broker.live_orders() and not await cold_broker.positions(account)
            finally:
                cold_writer.close();await cold_oms.close();await asyncio.sleep(0);cold_journal.close()
            # Actual runtime terminal producer, then original lifecycle-last
            # V4 terminal writer. No synthetic sequence or terminal row graft.
            await runtime.finish('completed')
            terminal_sequence=journal.latest_sequence(source.run_id)
            terminal_capture=(portfolio.capture_recovery_snapshot(account,state_revision=terminal_sequence,snapshot_at=at),)
            terminal=await publisher.enqueue_terminal(terminal_capture)
            assert terminal.last_sequence==terminal_sequence
            terminal_prefix=load_verified_v4_prefix(client,source.run_id)
            assert terminal_prefix.last_sequence==terminal_sequence
            assert terminal_prefix.status=='completed'
            accounts=client.tables['trading_backtest_account_snapshot_v2']
            assert len(accounts)==1 and accounts[0]['account_id']==account
            assert not journal.unfenced_records()
            assert (await broker.account_ledger(account)).cashbalance==10000.
            assert not portfolio.reservations and not oms._groups
        finally:
            writer.close();await oms.close();await asyncio.sleep(0);journal.close()
    from src.backend import backtest_fixed_structural_lot_certification_v9 as seal
    if not seal.REVIEWED_SOURCE_AST:
        pytest.skip('Actual86 controller/cold positive requires proposed or approved source snapshot')
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit=os.environ['FIXED_LOT_PROPOSED_HEAD'];real=subprocess.check_output
        monkeypatch.setattr(subprocess,'check_output',lambda args,**kw:(commit+'\n').encode() if args==['git','rev-parse','HEAD'] else b'' if args==['git','status','--porcelain'] else real(args,**kw))
    asyncio.run(run())


def journal_lease_keeper():
    """Controlled atomic Keeper transport supports lease and dispatch contracts."""
    from tests.test_live_signal_completion_keeper import FakeKazoo,Transaction,NoNodeError,BadVersionError,NodeExistsError
    class JournalTransaction(Transaction):
        def delete(self,path,version):
            self.ops.append(('delete',path,version))
        def commit(self):
            if self.client.before_commit is not None:
                hook,self.client.before_commit=self.client.before_commit,None
                hook()
            try:
                for op in self.ops:
                    node=self.client.nodes.get(op[1])
                    if op[0]=='create' and node is not None:raise NodeExistsError(op[1])
                    if op[0] in {'check','set','delete'}:
                        if node is None:raise NoNodeError(op[1])
                        expected=op[2] if op[0] in {'check','delete'} else op[3]
                        if node[1]!=expected:raise BadVersionError(op[1])
            except Exception as error:return [error]
            for op in self.ops:
                if op[0]=='delete':del self.client.nodes[op[1]]
                elif op[0]=='set':
                    old=self.client.nodes[op[1]];self.client.nodes[op[1]]=(op[2],old[1]+1,old[2])
                elif op[0]=='create':self.client.nodes[op[1]]=(op[2],0,self.client.client_id[0] if op[3] else 0)
            return [None]*len(self.ops)
    class JournalKeeper(FakeKazoo):
        def transaction(self):return JournalTransaction(self)
    return JournalKeeper()


def test_controlled_keeper_atomic_delete_failure_preserves_other_changes():
    keeper=journal_lease_keeper();keeper.create('/head',b'head');keeper.create('/operation',b'operation')
    transaction=keeper.transaction();transaction.set_data('/head',b'changed',version=0)
    transaction.delete('/operation',version=1)
    assert isinstance(transaction.commit()[0],Exception)
    assert keeper.get('/head')[0]==b'head' and keeper.get('/operation')[0]==b'operation'
