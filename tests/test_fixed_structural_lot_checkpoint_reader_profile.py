"""Strategy90 actual-source factory and actor proof; controlled transports explicit."""

def test_actual_checkpoint_reader_credentials_and_issued_profile(monkeypatch):
    """Real factory/profile validation; only private credentials and HTTP transport controlled."""
    from src.trading_runtime import arte_journal_writer as api
    from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
    from research.mlops import clickhouse
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        head = os.environ['FIXED_LOT_PROPOSED_HEAD']
        original = subprocess.check_output
        monkeypatch.setattr(subprocess, 'check_output', lambda args, **kw:
            (head+'\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else original(args, **kw))
    actual, request, plans = selected(monkeypatch, actual_loader=True)
    profile = issue_fixed_structural_lot_profile(actual.operation)
    observed = []
    def credentials(prefix, path):
        principal = ('backtest_v4_fixed_structural_lot_runner'
                     if prefix == 'BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CLICKHOUSE_'
                     else 'backtest_v4_runner')
        return 'http://127.0.0.1:8123', principal, 'controlled-test-secret'
    class Http:
        def __init__(self, url, user, password, **kwargs):
            self.user = user
            observed.append(user)
        def execute(self, sql):
            if sql == 'SELECT currentUser()': return self.user
            assert sql == 'SELECT count() FROM arte.trading_fixed_structural_lot_configuration_node_v1 LIMIT 1'
            return '0'
        def close(self): pass
    monkeypatch.setenv('BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CREDENTIAL_FILE', 'controlled-private-path')
    monkeypatch.setattr(api, '_dedicated_clickhouse_credentials', credentials)
    monkeypatch.setattr(clickhouse, 'ClickHouseHttpClient', Http)
    legacy = api.backtest_v4_operator_client_from_env()
    assert legacy.execute('SELECT currentUser()') == 'backtest_v4_runner'
    dedicated = api.backtest_v4_operator_client_from_env(fixed_structural_lot_profile=profile)
    assert dedicated.execute('SELECT currentUser()') == 'backtest_v4_fixed_structural_lot_runner'
    assert dedicated.fixed_structural_lot_profile is profile
    dedicated.execute('SELECT count() FROM arte.trading_fixed_structural_lot_configuration_node_v1 LIMIT 1')
    with pytest.raises(ValueError, match='issued'):
        api.backtest_v4_operator_client_from_env(fixed_structural_lot_profile=object())
    assert len(observed) == 2


def test_genuine_checkpoint_reader_owner_binding_and_foreign_inventory(monkeypatch):
    """Genuine actors and issued profile; controlled source/market/storage fixtures, no P&L claim."""
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        head=os.environ['FIXED_LOT_PROPOSED_HEAD'];original=subprocess.check_output
        monkeypatch.setattr(subprocess,'check_output',lambda args,**kw:
            (head+'\n').encode() if args==['git','rev-parse','HEAD'] else
            b'' if args==['git','status','--porcelain'] else original(args,**kw))
    async def exercise():
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
        from src.trading_runtime.domain import TradingMode,InstrumentContract
        from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy
        from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
        from src.trading_runtime import arte_journal_writer as writer_api
        from src.trading_runtime.original_risk_diagnostic_profile import declared_contract_runner_options
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
        from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
        actual,request,plans,config,client,flat=published(monkeypatch,actual_loader=True,exclusive_writer=True)
        entry=request.entry.proposal;at=request.intent.event_time
        journal=BacktestMemoryJournal(run_id=config.run_id)
        broker=SimulatedBrokerAdapter(config.account_ids,SimulationConfig(initial_cash=10000.),mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
        account=PortfolioAccountProfile('cash',entry.account_id,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        portfolio=PortfolioManagementEngine((account,),journal=journal,run_id=config.run_id,strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,event_clock=lambda:at)
        planner=RuntimeIbkrStrategyOrderPlanner({entry.ticker:InstrumentContract(entry.ticker,1,entry.ticker,'STK','USD')},strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,run_id=config.run_id)
        runtime=TradingRuntime(config,broker,SimpleNamespace(strategy_id=config.strategy_id,revision=config.strategy_revision,automatic=True),journal,portfolio=portfolio,intent_planner=planner)
        actual.bind_runtime(runtime)
        monkeypatch.setattr(writer_api,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_api,'journal_permission_preflight',lambda *a,**k:None)
        client.fixed_structural_lot_profile=actual.profile
        writer=writer_api.ArteJournalWriter(client,run_id=config.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),run_month=actual.operation.source.session_date.replace(day=1),expected_config=flat,fixed_market_parent_plan=plans.market)
        try:
            await runtime.initialize();actual.operation.bind_publisher(publisher)
            publisher.bind_first_price_source(actual.operation.source.price_authority)
            owner=NativeFixedStructuralLotManagement(operation=actual.operation,publisher=publisher,client=client)
            contract=numbered_fixed_strategy(90)
            options=dict(owner=owner,publisher=publisher,run_id=config.run_id)
            assert declared_contract_runner_options(contract,**options)=={'fixed_structural_lot_profile':actual.profile}
            original_publisher=owner.publisher;owner.publisher=None
            try:
                with pytest.raises(ValueError):declared_contract_runner_options(contract,**options)
            finally:owner.publisher=original_publisher
            for delta in (dict(owner=None),dict(owner=SimpleNamespace(operation=actual.operation)),dict(publisher=SimpleNamespace(writer=writer)),dict(run_id=str(uuid4()))):
                with pytest.raises(ValueError):declared_contract_runner_options(contract,**{**options,**delta})
            saved=publisher._first_price_source;publisher._first_price_source=object()
            try:
                with pytest.raises(ValueError):declared_contract_runner_options(contract,**options)
            finally:publisher._first_price_source=saved
            for foreign in (None,issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(actual.operation.source))):
                client.fixed_structural_lot_profile=foreign
                with pytest.raises(ValueError):declared_contract_runner_options(contract,**options)
            client.fixed_structural_lot_profile=actual.profile
            assert declared_contract_runner_options(numbered_fixed_strategy(42))=={}
            assert declared_contract_runner_options(numbered_fixed_strategy(57))=={'entry_spread_risk':True}
            assert declared_contract_runner_options(numbered_fixed_strategy(89))=={}
        finally:
            client.fixed_structural_lot_profile=actual.profile
            if runtime.order_manager is not None:await runtime.order_manager.close()
            for task in (runtime._broker_stream_task,runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            writer.close();journal.close()
    asyncio.run(exercise())


def test_all_three_checkpoint_factories_forward_exact_owner_publisher_run():
    """Structural boundary regression; genuine profile/factory behavior tested separately."""
    import ast
    path=Path(__file__).resolve().parents[1]/'src/backend/replay_run_service.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    controller=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ReplayRunController')
    for name in ('_confirm_profit_arming_checkpoint','_confirm_liquidity_fade_checkpoint','_confirm_original_risk_checkpoint'):
        method=next(n for n in controller.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        calls=[n for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='declared_contract_runner_options']
        assert len(calls)==1
        call=calls[0]
        assert ast.unparse(call.args[0])=='manager.contract'
        assert {k.arg:ast.unparse(k.value) for k in call.keywords}=={'owner':"getattr(manager, '_fixed_lot_owner', None)",'publisher':'publisher','run_id':'self.run_id'}
from dataclasses import replace
import json


def test_actual_public_app_revision_selector_and_certifier(monkeypatch):
    """Actual appâ†’configuration serviceâ†’selectorâ†’typed configuration certifier.

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
            if re.search(r'strategy_number=90\b', query):
                assert self.envelope is not None
                if 'configuration_release' in query:
                    e = self.envelope
                    rows = [dict(release_attempt_id='00000000-0000-0000-0000-000000000088',
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
    reader.envelope = compile_registered_fixed_structural_lot_configuration(parent,number=90,
        approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
        approval_reference='explicit offline configuration transport; no source admission')
    own = certify_numbered_configuration(reader, 89)
    monkeypatch.setattr(backtest_market_data,'readonly_clickhouse_client',lambda **_:reader)
    observed = []
    monkeypatch.setattr(app_module,'backtest_preflight',lambda **kwargs:
        observed.append(kwargs['configuration_revision']) or dict(configuration_revision=kwargs['configuration_revision']))
    for number, revision in ((42,parent.revision()),(86,captured['published86_identity']),(89,own.revision())):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',
            session_count=1,initial_cash=10000,configuration_revision_id=revision['revision_id'])
        result = app_module._trading_historical_preflight_payload(request)
        assert result['configuration_revision']['revision_id'] == revision['revision_id']
        assert result['configuration_revision']['revision'] == number
    before = len(reader.queries)
    for identity in ('strategy-one-43:'+own.attempt_id,'strategy-one-44:'+own.attempt_id,
                     'strategy-one-45:'+own.attempt_id,'strategy-one-99999:'+own.attempt_id,
                     'strategy-one-088:'+own.attempt_id,'strategy-one-90:'+'-'*36,
                     'candidate-89'):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',
            configuration_revision_id=identity)
        with pytest.raises(app_module.HTTPException,match='Unknown immutable'):
            app_module._trading_historical_preflight_payload(request)
    assert len(reader.queries) == before
    for extra in (dict(configuration_revision_id='strategy-one-90:00000000-0000-0000-0000-000000000089'),
                  dict(configuration_revision_id=own.revision()['revision_id'],run_plan_id='foreign')):
        request = app_module.HistoricalPreflightRequest(mode='backtest',anchor_date='2026-08-04',**extra)
        with pytest.raises(app_module.HTTPException,match='immutable release'):
            app_module._trading_historical_preflight_payload(request)
    assert len(observed) == 3


from test_fixed_structural_lot_interval_validator_v2 import ordinal_transport_plan


def selected(monkeypatch, *, actual_loader=False, number=90, version=12):
    from importlib import import_module
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    from test_fixed_structural_lot_source_v2 import inputs
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    source = import_module(f'src.backend.backtest_fixed_structural_lot_source_v{version}')
    from src.backend import backtest_fixed_structural_lot_source_v5 as scope_owner
    native = import_module(f'src.backend.backtest_fixed_structural_lot_native_v{version}')
    from src.backend import backtest_fixed_structural_lot_native as owner
    session = import_module(f'src.backend.backtest_fixed_structural_lot_execution_v{version}')
    from src.backend import backtest_strategy_one_execution as execution
    derive_fixed_structural_lot_release = import_module(f'src.trading_runtime.fixed_structural_lot_release_v{version}').derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=number))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source if name == '_load_quotes' else scope_owner, name, getattr(previous, name))
    monkeypatch.setattr(scope_owner, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent, _, _, parent_release = declarations()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release, release=numbered_strategy(number), policy=old.policy.payload(), approved_code_commit=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(), approved_code_fingerprint=backend_source_fingerprint(), approval_reference='controlled immutable installation seam')['payload'])
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
    actual = session.prepare_fixed_structural_lot_session(plans=plans, number=number, run_id=old.run_id, session_date=old.session_date, market=plans.market, candidates=plans.candidates, entry=plans.entry, seeds=plans.seeds, through_boundary_ms=57600000, client_factory=Client)
    actual.require(market=plans.market, candidates=plans.candidates, entry=plans.entry, through_boundary_ms=57600000, run_id=old.run_id, number=number)
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
    from src.backend import backtest_fixed_structural_lot_native_v12 as native
    from src.trading_runtime.fixed_structural_lot_release_v12 import derive_fixed_structural_lot_release
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
    from src.backend import backtest_fixed_structural_lot_certification_v12 as seal
    native, parent, own = _actual_native_factory_declaration(monkeypatch, 90)
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit = os.environ['FIXED_LOT_PROPOSED_HEAD']
        real = subprocess.check_output
        monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
            (commit + '\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else real(args, **kwargs))
    if not seal.REVIEWED_SOURCE_AST:
        with pytest.raises(ValueError, match='unapproved'):
            native.load_installed_configuration(object(), number=90, parent=parent)
        return
    loaded, policy, proof = native.load_installed_configuration(object(), number=90, parent=parent)
    assert loaded is own and policy.payload() == actual_policy_payload()
    assert len(proof) == 64 and all(c in '0123456789abcdef' for c in proof)
    # No certification function, installed loader, or current-source verifier is replaced.
    assert native.verify_current_installed_source.__module__ == 'src.backend.backtest_fixed_structural_lot_native_v5'


def test_actual_native_factory_reaches_runtime_start_and_projection_without_certificate_stubs(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_certification_v12 as seal
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
                number=90, actual_factory_module='src.backend.backtest_fixed_structural_lot_native_v12',
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
    from src.backend import backtest_fixed_structural_lot_native_v12 as native
    root = Path(native.__file__).resolve().parents[2]
    active = ('src/backend/backtest_fixed_structural_lot_native_v12.py',
        'src/backend/backtest_fixed_structural_lot_source_v12.py',
        'src/backend/backtest_fixed_structural_lot_execution_v12.py',
        'src/backend/backtest_fixed_structural_lot_empty_v12.py',
        'src/trading_runtime/fixed_structural_lot_release_v12.py',
        'src/trading_runtime/strategy_eighty_nine_release.py',
        'src/trading_runtime/strategy_eighty_nine_contract.py',
        'src/trading_runtime/fixed_structural_lot_causal_clock.py')
    for relative in active:
        source = (root / relative).read_text(encoding='utf-8')
        assert unresolved_source_imports(root, relative, source) == []
        injected = source + '\nfrom src.backend.future_unresolved_native_certificate import issue\n'
        assert unresolved_source_imports(root, relative, injected)
    relative = 'src/backend/backtest_fixed_structural_lot_native_v5.py'
    retained = unresolved_source_imports(root, relative, (root / relative).read_text(encoding='utf-8'))
    assert len(retained) == 1 and retained[0][2] == 'src.backend.backtest_fixed_v5_certification'


def published(monkeypatch, *, publish=True, actual_loader=False, exclusive_writer=False,
              number=90, version=12, transport_class=None):
    actual, request, plans = selected(monkeypatch, actual_loader=actual_loader, number=number, version=version)
    source = actual.operation.source
    config = RunConfig(mode=RunMode.BACKTEST, strategy_id=request.strategy_id,
        strategy_revision=request.revision, account_ids=(request.entry.proposal.account_id,),
        anchor_date=source.session_date, run_id=source.run_id, write_progress_checkpoints=False)
    ExactDecisionTransport = BatchedDecisionTransport
    from src.backend.backtest_v4_run_context import fixed_v4_context_rows
    from src.trading_runtime.arte_journal_writer import publish_typed_run, publish_typed_run_context
    client = (transport_class or ExactDecisionTransport)()
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from tests.test_arte_typed_insert_dispatch import Keeper
    keeper = journal_lease_keeper() if exclusive_writer else Keeper()
    client.typed_insert_dispatch = TypedInsertDispatch(keeper)
    client.typed_insert_strict = True
    if publish:
        client.typed_insert_dispatch.initialize_new_run(source.run_id)
    if exclusive_writer:
        from src.trading_runtime.keeper_session import ManagedKeeperSession
        from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
        keeper.add_listener = lambda listener: None
        session = ManagedKeeperSession(keeper)
        session._on_state('CONNECTED')
        client.manager_keeper_session = session
        client.backtest_v4_lease = BacktestV4KeeperLease.acquire(
            session, run_id=source.run_id, owner_id=str(uuid4()))
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
    from src.backend import backtest_fixed_structural_lot_certification_v12 as seal
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
        actual,request,plans,config,client,flat=published(monkeypatch,actual_loader=True,exclusive_writer=True)
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
            from src.trading_runtime.arte_oms_projection import load_latest_committed_oms_groups
            first=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=contexts)
            assert first and client.batched_queries
            assert any(query.startswith('SELECT family_name,payload FROM (') for query in client.batched_queries)
            before=len(client.batched_queries)
            second=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=contexts)
            assert second==first and len(client.batched_queries)==before
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



def test_release_90_changes_only_declared_checkpoint_reader_profile():
    from src.trading_runtime.strategy_registry import numbered_strategy,OPERATION_CHECKPOINT_READER_RULE
    previous=numbered_strategy(89);current=numbered_strategy(90)
    assert current.input_contracts==previous.input_contracts
    assert current.rule_set_contracts==(*previous.rule_set_contracts,OPERATION_CHECKPOINT_READER_RULE)
    assert current.evaluation_interval==previous.evaluation_interval
    from tests.test_fixed_structural_lot_native import declarations
    from src.trading_runtime.fixed_structural_lot_release_v11 import derive_fixed_structural_lot_release as old
    from src.trading_runtime.fixed_structural_lot_release_v12 import derive_fixed_structural_lot_release as new
    parent,_,_,parent_release=declarations()
    args=dict(parent_release=parent_release,policy=actual_policy_payload(),approved_code_commit='a'*40,approved_code_fingerprint='b'*64,approval_reference='pure tree equivalence only')
    assert old(parent,release=previous,**args)['payload']['strategy']['parameters']==new(parent,release=current,**args)['payload']['strategy']['parameters']


def test_exact_parent_full_ast_restorations_and_foreign_delta_rejection():
    import ast
    from src.backend.backtest_fixed_structural_lot_compatibility_v12 import REVIEWED_PARENT_DELTAS,restore_reviewed_parent_source
    root=Path(__file__).resolve().parents[1]
    for relative,(current,baseline,_) in REVIEWED_PARENT_DELTAS.items():
        raw=(root/relative).read_text(encoding='utf-8')
        restored=restore_reviewed_parent_source(raw,relative)
        actual=subprocess.check_output(['git','show','c24ebbbf1ddb9a58ec5f3ac96b5c00e7def9d0b5:'+relative],cwd=Path(os.environ.get('FIXED_LOT_PARENT_SOURCE_REPOSITORY',str(root)))).decode('utf-8')
        assert ast.dump(ast.parse(restored))==ast.dump(ast.parse(actual)),relative
        changed=raw+'\nforeign_unreviewed_source_change = True\n'
        assert restore_reviewed_parent_source(changed,relative)==changed


from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo

def test_actual_89_selected_controller_checkpoint_and_fresh_cold_actors(monkeypatch):
    async def run():
        from tests import test_fixed_structural_lot_empty as fixture
        from tests.test_backtest_strategy_one_candidate_store import _market,THROUGH
        from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan,RULE_DIGEST
        from src.backend.backtest_strategy_one_plan import StrategyOneFixedPlans
        from src.backend.backtest_market_data import project_empty_market_day_plan
        from src.backend import backtest_fixed_structural_lot_native_v12 as native
        from src.backend.backtest_fixed_structural_lot_execution_v12 import prepare_fixed_structural_lot_session
        from src.backend.backtest_fixed_structural_lot_execution import bind_fixed_structural_lot_manager
        native,parent,own=_actual_native_factory_declaration(monkeypatch,90)
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
        prepared=prepare_fixed_structural_lot_session(plans=plans,number=90,run_id=str(uuid4()),
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
        ExactDecisionTransport = BatchedDecisionTransport
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
            from src.backend import backtest_fixed_structural_lot_empty_v12 as empty
            reader=fixture.EmptyReader()
            fresh=empty.prepare_empty_fixed_structural_lot_source(reader,number=90,run_id=source.run_id,
                session_date=source.session_date,plans=plans,
                through_boundary_ms=source.through_boundary_ms)
            fresh_operation=NativeFixedStructuralLotOperation(fresh)
            class FreshColdTransport:
                fixed_structural_lot_profile=profile_module.issue_fixed_structural_lot_profile(fresh_operation)
                v4_batched_detail_readback=True
                def execute(self,sql,*args,**kwargs):return client.execute(sql,*args,**kwargs)
                def close(self):pass
                # Controlled storage/owned Keeper transport only. The installed
                # source profile above is freshly issued, never copied.
                def __getattr__(self,name):return getattr(client,name)
            cold_client=FreshColdTransport()
            image=load_cold_manager_image(cold_client,session,source=fresh,fixed_lot_contexts=())
            assert image.selected_positions==() and image.inherited.positions==image.inherited.submitted==()
            assert image.sequence==record.sequence and image.source is fresh
            from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
            from src.trading_runtime.strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
            from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            from src.backend.backtest_fixed_journal_bootstrap import restore_fixed_structural_lot_native_manager
            prefix=load_verified_v4_prefix(cold_client,source.run_id)
            recovered=recover_portfolio_engine_state(cold_client,run_id=source.run_id,profiles=(profile,),
                state_revisions={account:image.sequence},cutoff_at=at)
            cold_broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
            await cold_broker.initialize()
            rows=load_unattested_broker_match_snapshot(cold_client,run_id=source.run_id,checkpoint_sequence=image.sequence)
            cold_broker.restore_checkpoint_state(reconstruct_broker_match_state(rows,requests_by_broker_id={},quotes={}))
            cold_journal=BacktestMemoryJournal(run_id=source.run_id,initial_sequence=prefix.last_sequence)
            cold_writer=writer_module.ArteJournalWriter(cold_client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
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
            cold_owner=NativeFixedStructuralLotManagement(operation=fresh_operation,
                publisher=cold_publisher,client=cold_client)
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
    from src.backend import backtest_fixed_structural_lot_certification_v12 as seal
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


from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport as _BaseDecisionTransport
class BatchedDecisionTransport(_BaseDecisionTransport):
    v4_batched_detail_readback=True
    def __init__(self):
        super().__init__()
        self.batched_queries=[]
    def execute(self,sql,*,query_id=None):
        if type(sql) is str and (sql.startswith('SELECT family_name,payload FROM (') or sql.startswith('(SELECT ')):
            import json,re
            outer=sql.startswith('SELECT family_name,payload FROM (')
            body=sql[len('SELECT family_name,payload FROM ('):-len(') FORMAT JSONEachRow')] if outer else sql[:-len(' FORMAT JSONEachRow')]
            parts=body.split(' UNION ALL ')
            assert 1<=len(parts)<=8
            envelopes=[]
            for part in parts:
                match=re.fullmatch(r"\(SELECT '([a-z0-9_]+)' AS family_name, toJSONString\(tuple\(([^()]+)\)\) AS payload FROM arte\.([a-z0-9_]+) (WHERE run_id='[^']+' AND batch_id=toUUID\('[^']+'\) )LIMIT ([0-9]+)\)",part)
                assert match,sql
                name,columns,table,filters,limit=match.groups();assert name==table
                rows=super().execute(f'SELECT {columns} FROM arte.{table} {filters}LIMIT {limit} FORMAT JSONEachRow')
                for line in rows.splitlines():
                    row=json.loads(line)
                    envelopes.append(dict(family_name=name,payload=json.dumps([row[col] for col in columns.split(',')])))
            self.batched_queries.append(sql)
            self.selects.append(sql)
            return '\n'.join(json.dumps(row) for row in envelopes)
        if type(sql) is str and ('FROM arte.trading_execution_v1 ' in sql and 'AND broker_order_id IN (' in sql
                or 'FROM arte.trading_event_v1 ' in sql and 'AND entity_id IN (' in sql):
            import json,re
            execution_shape=r"SELECT [a-z0-9_,]+ FROM arte\.trading_execution_v1 WHERE run_id='[^']+' AND broker_order_id IN \('[^']+'(?:,'[^']+')*\) AND batch_id IN \(SELECT batch_id FROM arte\.trading_commit_v4 WHERE run_id='[^']+' AND last_sequence<=\d+\) ORDER BY source_event_time,record_id LIMIT \d+ FORMAT JSONEachRow"
            event_shape=r"SELECT [a-z0-9_,]+ FROM arte\.trading_event_v1 WHERE run_id='[^']+' AND sequence>\d+ AND sequence<=\d+ AND category='execution' AND entity_type='fill' AND entity_id IN \('[^']+'(?:,'[^']+')*\) AND batch_id IN \(SELECT batch_id FROM arte\.trading_commit_v4 WHERE run_id='[^']+' AND last_sequence<=\d+\) ORDER BY sequence LIMIT \d+ FORMAT JSONEachRow"
            assert re.fullmatch(execution_shape,sql) or re.fullmatch(event_shape,sql),sql
            table=re.search(r'FROM arte\.(trading_execution_v1|trading_event_v1) ',sql).group(1)
            run=re.search(r"WHERE run_id='([^']+)'",sql).group(1)
            fence=re.search(r"AND batch_id IN \(SELECT batch_id FROM arte\.trading_commit_v4 WHERE run_id='([^']+)' AND last_sequence<=(\d+)\)",sql)
            assert fence and fence.group(1)==run,sql
            limit=re.search(r' LIMIT (\d+) FORMAT JSONEachRow$',sql)
            assert limit and 1<=int(limit.group(1))<=100001,sql
            committed={v['batch_id'] for v in self.tables.get('trading_commit_v4',())
                if v['run_id']==run and int(v['last_sequence'])<=int(fence.group(2))}
            rows=[v for v in self.tables.get(table,()) if v['run_id']==run and v['batch_id'] in committed]
            field='broker_order_id' if table=='trading_execution_v1' else 'entity_id'
            wanted=re.search(rf'AND {field} IN \(([^)]+)\)',sql)
            assert wanted,sql
            identities=set(re.findall(r"'([^']+)'",wanted.group(1)))
            rows=[v for v in rows if str(v[field]) in identities]
            if table=='trading_event_v1':
                assert "AND category='execution' AND entity_type='fill' " in sql,sql
                lower=re.search(r'AND sequence>(\d+)',sql);upper=re.search(r'AND sequence<=(\d+)',sql)
                assert lower and upper and 'ORDER BY sequence ' in sql,sql
                rows=[v for v in rows if v['category']=='execution' and v['entity_type']=='fill'
                    and int(lower.group(1))<int(v['sequence'])<=int(upper.group(1))]
                rows.sort(key=lambda v:int(v['sequence']))
            else:
                assert 'ORDER BY source_event_time,record_id ' in sql,sql
                rows.sort(key=lambda v:(v['source_event_time'],v['record_id']))
            columns=sql[len('SELECT '):].split(' FROM ',1)[0].split(',')
            self.selects.append(sql)
            return '\n'.join(json.dumps({key:v[key] for key in columns}) for v in rows[:int(limit.group(1))])
        if type(sql) is str and 'FROM arte.trading_commit_v4 ' in sql and 'AND first_sequence>=' in sql:
            import json,re
            match=re.fullmatch(r"SELECT batch_id,prior_batch_id,first_sequence,last_sequence FROM arte\.trading_commit_v4 WHERE run_id='([^']+)' AND first_sequence>=(\d+) AND last_sequence<=(\d+) ORDER BY first_sequence LIMIT (\d+) FORMAT JSONEachRow",sql)
            assert match,sql
            run,lower,upper,limit=match.groups();lower,upper,limit=map(int,(lower,upper,limit))
            assert 1<=lower<=upper and 1<=limit<=100001
            raw=super().execute(sql,query_id=query_id)
            rows=[json.loads(line) for line in raw.splitlines() if line]
            rows=sorted((v for v in rows if lower<=int(v['first_sequence'])<=int(v['last_sequence'])<=upper),key=lambda v:int(v['first_sequence']))
            return '\n'.join(json.dumps(v) for v in rows[:limit])
        return super().execute(sql,query_id=query_id)

class ScaleStrippedSnapshotTransport(BatchedDecisionTransport):
    """Controlled ClickHouse Decimal text formatting, without changing stored facts."""
    def __init__(self):
        super().__init__()
        self.scale_stripped_cells = 0

    def execute(self, sql, *, query_id=None):
        result = super().execute(sql, query_id=query_id)
        if (not isinstance(sql, str) or not sql.startswith(('SELECT ', '(SELECT ')) or
                not sql.endswith(' FORMAT JSONEachRow') or not result):
            return result
        import json, re
        from src.trading_runtime.arte_journal_writer import _CONTRACTS
        from src.trading_runtime.fixed_structural_lot_snapshot import ROOT, LOT, RESISTANCE
        from src.trading_runtime.strategy_one_management_snapshot import SOURCE, BREAK, HIGH, CLOSED, FIRST_HELD
        from src.trading_runtime.strategy_one_protection_snapshot import TABLES
        families = {v.name for v in (ROOT, LOT, RESISTANCE, SOURCE, BREAK, HIGH, CLOSED, FIRST_HELD, *TABLES)}

        def shorten(row, name):
            if name not in families:
                return row
            for key, kind in _CONTRACTS[name].columns:
                if 'Decimal(' in kind and key in row and isinstance(row[key], str):
                    text = row[key]
                    value = text.rstrip('0').rstrip('.') if '.' in text else text
                    if value != text:
                        self.scale_stripped_cells += 1
                        row[key] = value
            return row

        match = re.search(r'FROM arte\.([a-z0-9_]+)', sql)
        rows = [json.loads(line) for line in result.splitlines() if line]
        for row in rows:
            if set(row) == {'family_name', 'payload'}:
                name = row['family_name']
                if name in families:
                    columns = [key for key, _ in _CONTRACTS[name].columns]
                    values = json.loads(row['payload'])
                    assert len(columns) == len(values)
                    decoded = shorten(dict(zip(columns, values)), name)
                    row['payload'] = json.dumps([decoded[key] for key in columns])
            elif match:
                shorten(row, match.group(1))
        return '\n'.join(json.dumps(row) for row in rows)


def test_scale_stripped_snapshot_transport_preserves_scalar_preflight(monkeypatch):
    monkeypatch.setattr(BatchedDecisionTransport, 'execute',
                        lambda self, sql, **kwargs: 'backtest_v4_fixed_structural_lot_runner')
    client = ScaleStrippedSnapshotTransport()
    assert client.execute('SELECT currentUser()') == 'backtest_v4_fixed_structural_lot_runner'
    assert client.scale_stripped_cells == 0


def test_strict_warm_proxy_keeps_exact_read_only_and_argument_guards():
    from src.trading_runtime.fixed_structural_lot_warm_proof import _ImmutableReads
    class Reader:
        def execute(self,sql):return ''
    proxy=_ImmutableReads(Reader(),{})
    for sql in ('INSERT INTO arte.x VALUES (1)','WITH x AS (SELECT 1) SELECT x','(SELECT 1) FORMAT JSONEachRow','SELECT\n1'):
        with pytest.raises(ValueError,match='exact SELECT'):proxy.execute(sql)
    for args,kwargs in (((1,),{}),((),{'query_id':'foreign'})):
        with pytest.raises(ValueError,match='exact SELECT'):proxy.execute('SELECT 1',*args,**kwargs)
    assert proxy.execute('SELECT 1')==''

def test_batched_helper_rejects_foreign_context_before_transport():
    from src.trading_runtime.arte_journal_commit_v4 import _batched_detail_rows_v4
    class Reader:
        def execute(self,sql):raise AssertionError('Foreign context must fail before transport')
    with pytest.raises(ValueError,match='foreign source context'):
        _batched_detail_rows_v4(Reader(),(),'',fixed_lot_context=SimpleNamespace(source=None))

@pytest.mark.parametrize("number,version,strip_decimal", [(90,12,False),(92,13,True)])
def test_actual_earned_profit_arm_selected_checkpoint_and_historical_products(monkeypatch,number,version,strip_decimal):
    quote_offset_us=25515
    from importlib import import_module
    seal=import_module(f"src.backend.backtest_fixed_structural_lot_certification_v{version}")
    if not seal.REVIEWED_SOURCE_AST:
        pytest.skip('Actual full source certification positive runs on reviewed proposal and clean committed source')
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit=os.environ['FIXED_LOT_PROPOSED_HEAD'];real=subprocess.check_output
        monkeypatch.setattr(subprocess,'check_output',lambda args,**kw:
            (commit+'\n').encode() if args==['git','rev-parse','HEAD'] else
            b'' if args==['git','status','--porcelain'] else real(args,**({**kw,'cwd':os.environ['FIXED_LOT_PARENT_SOURCE_REPOSITORY']} if args[:2]==['git','show'] and 'cwd' not in kw else kw)))
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
        actual,request,plans,config,client,flat=published(monkeypatch,actual_loader=True,exclusive_writer=True,number=number,version=version,
            transport_class=ScaleStrippedSnapshotTransport if strip_decimal else None)
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
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),run_month=actual.operation.source.session_date.replace(day=1),expected_config=flat,fixed_market_parent_plan=plans.market)
        from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
        from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
        from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
        class ControlledCompletedEvidence:
            boundary=entry.boundary_ms
            async def management_evidence(self,ticker,resolutions,*,boundary_ms):
                self.boundary=boundary_ms
                return StrategyOneManagementEvidence(ticker,boundary_ms,10.,10.01,True,None,None,(),())
            def capture_recovery_state(self):return StrategyOneEvidenceState(self.boundary,(),(),())
        evidence=ControlledCompletedEvidence()
        manager=StrategyOneManagementRunner(runtime=runtime,evidence=evidence,tick_for_ticker=lambda ticker:.01)
        try:
            await runtime.initialize();actual.operation.bind_publisher(publisher)
            publisher.bind_first_price_source(actual.operation.source.price_authority)
            owner=NativeFixedStructuralLotManagement(operation=actual.operation,publisher=publisher,client=client)
            manager.bind_fixed_structural_lot_management(owner)
            import polars as pl
            from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
            empty_activity=pl.DataFrame(schema={name:pl.String for name in
                ('source_build_id','session_date','ticker','source_attempt_id')}|{'boundary_ms':pl.UInt32,'trade_count':pl.UInt64})
            manager.bind_liquidity_fade_lookup(CompiledLiquidityFadeLookup(empty_activity,plan=plans.market,
                session_date=actual.operation.source.session_date,strategy_number=number),plans.market)
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
                    await manager.on_entry_proposal(entry)
                    assert owner.entries and manager._submitted
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
            with pytest.raises(ValueError,match='exceeds'):
                await owner.first_held((entry.account_id,entry.assignment_id,entry.ticker),boundary_ms=entry.boundary_ms)
            from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
            from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
            financial=StrategyOneFinancialView(entry.assignment_id,entry.account_id,entry.ticker,
                AssignmentStatus.MANAGING,StrategyPermissions(),float(group.filled_quantity),True,False,False,1)
            evidence.boundary=now_ms
            started=perf_counter();await manager.on_management(financial,{100:row},now_ms);elapsed=perf_counter()-started
            state=owner.states[(entry.account_id,entry.assignment_id,entry.ticker)]
            assert state.protection.boundary_ms==now_ms and now_ms%100==0
            assert state.roster.observed_boundary_ms==now_ms
            prefix,contexts=owner._prefix();args=owner._arguments(request,prefix,contexts)
            from src.trading_runtime.arte_oms_projection import load_latest_committed_oms_groups
            first=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=contexts)
            assert first and client.batched_queries
            assert any(query.startswith('SELECT family_name,payload FROM (') for query in client.batched_queries)
            before=len(client.batched_queries)
            second=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset((entry.account_id,)),fixed_lot_contexts=contexts)
            assert second==first and len(client.batched_queries)==before
            rows=project_fixed_structural_lot_snapshot(state,**args)
            restored=restore_fixed_structural_lot_snapshot(rows,entry=request.entry,**args)
            assert restored==state
            print('actual_native_first_held_seconds='+str(round(elapsed,6)))
            from decimal import Decimal
            threshold=int((2*Decimal(str(entry.reference_ask))-Decimal(str(entry.initial_stop)))*10000)
            for offset,high in ((200,100100),(300,threshold+100)):
                boundary=entry.boundary_ms+offset
                at=market_day_boundary(actual.operation.source.session_date,boundary)
                trade_us=int(at.timestamp()*1000000)-1000
                completed=dict(row,boundary_ms=boundary,bucket_index=row['bucket_index']+offset//100-1,
                    first_event_us=trade_us,last_event_us=trade_us,execution_volume=100000.,
                    execution_price_levels=({'price_int':100100,'volume':100000.},),ask_size=10000.,
                    close_int=100100,low_int=100100,high_int=high)
                # Completed high earns arming; carried executable quote stays below targets.
                await runtime.process_liquidity_boundary([completed],at=at)
                await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
                group=next(iter(runtime.order_manager._groups.values()))
                assert group.filled_quantity>0
                financial=replace(financial,position_quantity=float(group.filled_quantity),pending_entry=False)
                await manager.on_management(financial,{100:completed},boundary)
                evidence.boundary=boundary
            arm_boundary=entry.boundary_ms+300
            requests=manager.profit_arming_requests(boundary_ms=arm_boundary)
            assert len(requests)==1 and not manager._profit_arm_references
            from src.backend.replay_run_service import ReplayRunController
            controller=object.__new__(ReplayRunController)
            controller.definition=SimpleNamespace(mode=RunMode.BACKTEST,configuration_revision={})
            controller.run_id=config.run_id;controller.status='running'
            controller._journal=journal;controller._journal_publisher=publisher
            controller._runtime=runtime;controller._strategy_one_manager=manager
            controller._fixed_keeper_session=client.manager_keeper_session
            controller._account_map={'cash':entry.account_id};controller.processed_events=0;controller.warmup_events=0
            controller._processed_frames=0;controller._pending_passive_market_events=[]
            controller._source_cursor=dict(session_date=actual.operation.source.session_date.isoformat(),boundary_ms=arm_boundary,sequence=0)
            controller._frame_cursor={};controller._checkpoint_io_task=None
            controller.stream_snapshot=lambda:dict(status='running')
            controller._record_stage_time=lambda *a:None
            controller._restart_checkpoint_interval_events=lambda:100
            from src.trading_runtime import arte_journal_writer as writer_api
            class ReadOnlyBorrowed:
                def __getattr__(self,name):return getattr(client,name)
                def close(self):pass
            # Retain actual factory, credential selection and issued-profile validation.
            from research.mlops import clickhouse as http_api
            observed_reader_principals=[]
            def controlled_credentials(prefix, path_key):
                assert prefix == 'BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CLICKHOUSE_'
                return 'http://127.0.0.1:8123','backtest_v4_fixed_structural_lot_runner','controlled-test-secret'
            class ControlledReaderHttp(ReadOnlyBorrowed):
                def __init__(self,url,user,password,**kwargs):
                    assert user == 'backtest_v4_fixed_structural_lot_runner'
                    observed_reader_principals.append(user)
            monkeypatch.setenv('BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CREDENTIAL_FILE','controlled-private-path')
            monkeypatch.setattr(writer_api,'_dedicated_clickhouse_credentials',controlled_credentials)
            monkeypatch.setattr(http_api,'ClickHouseHttpClient',ControlledReaderHttp)
            references=await controller._confirm_profit_arming_checkpoint(requests,event_time=at)
            assert len(references)==1 and manager._profit_arm_references
            assert observed_reader_principals == ['backtest_v4_fixed_structural_lot_runner']
            from src.trading_runtime.selected_checkpoint_products import load_historical_checkpoint,require_historical_checkpoint
            prefix,contexts=owner._prefix()
            image=load_historical_checkpoint(client,prefix,source=actual.operation.source,
                sequence=references[0].checkpoint_sequence)
            assert require_historical_checkpoint(image,source=actual.operation.source) is image
            from src.trading_runtime.selected_checkpoint_products import _HistoricalReads,_HISTORICAL_READ_ISSUER
            historic_contexts=tuple(v for v in contexts if v.base.batch_id in image.prefix.batch_ids)
            historic_recoveries=tuple(v for v in getattr(client,'fixed_lot_recovery_contexts',()) if v[0] in image.prefix.batch_ids)
            historical_reader=_HistoricalReads(client,actual.operation.source,image.prefix,
                historic_contexts,historic_recoveries,issuer=_HISTORICAL_READ_ISSUER)
            assert historical_reader.backtest_v4_lease is None
            with pytest.raises(ValueError):historical_reader.execute('INSERT INTO arte.x VALUES (1)')
            for bad_prefix in (None,replace(image.prefix,run_id='foreign')):
                with pytest.raises(ValueError):
                    _HistoricalReads(client,actual.operation.source,bad_prefix,historic_contexts,
                        historic_recoveries,issuer=_HISTORICAL_READ_ISSUER)
            assert image.inherited.position_highs==manager.capture_state(boundary_ms=arm_boundary).position_highs
            print('actual_earned_arm_selected_checkpoint_sequence='+str(image.sequence))
            # A later completed 5s bucket earns the inherited giveback exit.
            # This exercises real runtime projection, source sealing and readback.
            exit_boundary=50_000
            at=market_day_boundary(actual.operation.source.session_date,exit_boundary)
            trade_us=int(at.timestamp()*1000000)-1000
            floor=Decimal(str(entry.reference_ask))+(Decimal(str(entry.reference_ask))-Decimal(str(entry.initial_stop)))/2
            floor_int=int(floor*10000)//100*100
            giveback_bid=float(Decimal(floor_int)/10000)
            giveback_ask=float(Decimal(floor_int+100)/10000)
            giveback_quote=dict(row,boundary_ms=exit_boundary,bucket_index=row['bucket_index']+(exit_boundary-now_ms)//100,
                first_event_us=trade_us,last_event_us=trade_us,quote_timestamp_us=trade_us,
                bid_int=floor_int,ask_int=floor_int+100,close_int=floor_int,low_int=floor_int,high_int=floor_int,
                execution_volume=100000.,execution_price_levels=({'price_int':floor_int,'volume':100000.},),ask_size=10000.)
            completed_five=dict(boundary_ms=exit_boundary,close_int=floor_int,price_valid=1,macd_line=0.,macd_signal=1.)
            await runtime.process_liquidity_boundary([giveback_quote],at=at)
            quantity=broker.position_quantity(entry.account_id,1,entry.ticker)
            assert quantity>0
            financial=replace(financial,position_quantity=quantity)
            evidence.boundary=exit_boundary
            # The evidence transport carries this completed quote, not a future high.
            original_evidence=evidence.management_evidence
            async def exit_evidence(ticker,resolutions,*,boundary_ms):
                return StrategyOneManagementEvidence(ticker,boundary_ms,giveback_bid,giveback_ask,True,None,None,(),())
            evidence.management_evidence=exit_evidence
            await manager.on_management(financial,{100:giveback_quote,5000:completed_five},exit_boundary)
            delegations=[dict(sequence=v.sequence,record_id=v.record_id,entity_type=v.entity_type,
                entity_id=v.entity_id,event_time=v.event_time.isoformat(),payload=v.payload)
                for v in journal.unfenced_records()
                if str(v.payload.get('event','')).startswith('protection_delegated_to_')]
            from src.trading_runtime.selected_checkpoint_products import managed_exit_metadata
            from src.trading_runtime.strategy_orders import canonical_runtime_metadata
            exits=[value for value in runtime.order_manager._groups.values() if value.intent.action=='exit']
            assert len(exits)==1
            exit_group=exits[0];exit_order=exit_group.orders[0]
            exit_metadata=canonical_runtime_metadata(exit_order,exit_group.intent)
            scope=dict(source=actual.operation.source,run_id=config.run_id,
                strategy_id=actual.operation.source._strategy_id,
                strategy_revision=actual.operation.source._revision)
            assert managed_exit_metadata(exit_group,exit_order,exit_metadata,**scope)==exit_metadata
            for mutated in ({**exit_metadata,'action':'enter_long'},
                            {**exit_metadata,'execution_role':'profit_target'}):
                with pytest.raises(ValueError):managed_exit_metadata(exit_group,exit_order,mutated,**scope)
            with pytest.raises(ValueError):
                managed_exit_metadata(exit_group,exit_order,exit_metadata,**{**scope,'run_id':'foreign'})
            with pytest.raises(ValueError):
                managed_exit_metadata(exit_group,replace(exit_order,quantity=exit_order.quantity+1),exit_metadata,**scope)
            await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
            profits=client.tables.get('trading_profit_giveback_v4',())
            assert len(profits)==1 and str(profits[0]['source_entry_intent_id'])==request.intent.intent_id
            assert int(profits[0]['source_manager_checkpoint_sequence'])==references[0].checkpoint_sequence
            prefix,contexts=owner._prefix()
            from src.trading_runtime.strategy_profit_giveback_source import load_profit_giveback_checkpoint
            persisted=load_profit_giveback_checkpoint(client,prefix,profits[0],financial,
                first_price_source=actual.operation.source.price_authority)
            assert persisted.position_highs==image.inherited.position_highs
            assert persisted.first_held_boundaries==image.inherited.first_held_boundaries
            from src.trading_runtime.selected_checkpoint_products import original_entry,original_link_matches
            stored,source_event,companion=original_entry(client,config.run_id,request.original.intent_id,
                prior_batch_id=prefix.last_batch_id,exit_batch_id=str(profits[0]['batch_id']),
                verified_prefix=prefix,first_price_source=actual.operation.source.price_authority)
            assert str(stored['intent_id'])==request.intent.intent_id
            assert original_link_matches(client,stored,companion,request.original.intent_id)
            with pytest.raises(ValueError):
                original_link_matches(client,stored,{**companion,'original_intent_id':str(uuid4())},request.original.intent_id)
            with pytest.raises(ValueError):
                require_historical_checkpoint(replace(image,sequence=image.sequence+1),source=actual.operation.source)
            # Genuine selected checkpoint while the independently published
            # profit exit is pending; fresh source and contexts come from rows.
            partial_boundary=exit_boundary+100
            at=market_day_boundary(actual.operation.source.session_date,partial_boundary)
            partial_us=int(at.timestamp()*1000000)-1000
            partial_bid_int=int(Decimal(str(exit_order.price))*10000)
            partial_quote=dict(giveback_quote,boundary_ms=partial_boundary,
                bucket_index=giveback_quote['bucket_index']+1,
                first_event_us=partial_us,last_event_us=partial_us,quote_timestamp_us=partial_us,
                bid_int=partial_bid_int,ask_int=partial_bid_int+100,
                first_bid_int=partial_bid_int,last_bid_int=partial_bid_int,
                first_ask_int=partial_bid_int+100,last_ask_int=partial_bid_int+100,
                close_int=partial_bid_int,low_int=partial_bid_int,high_int=partial_bid_int,
                bid_size=4.,ask_size=10000.,execution_volume=4.,
                execution_price_levels=({'price_int':partial_bid_int,'volume':4.},))
            await runtime.process_liquidity_boundary([partial_quote],at=at)
            partial_quantity=broker.position_quantity(entry.account_id,1,entry.ticker)
            expected_partial=4*broker.config.liquidity_participation
            assert 0<expected_partial<quantity
            assert partial_quantity==quantity-expected_partial and exit_group.filled_quantity==expected_partial
            financial=replace(financial,position_quantity=partial_quantity,pending_entry=False,pending_exit=True)
            evidence.boundary=partial_boundary
            await manager.on_management(financial,{100:partial_quote},partial_boundary)
            exit_boundary=partial_boundary
            controller._source_cursor['boundary_ms']=exit_boundary
            await controller._save_restart_checkpoint_responsive(at,require_fresh_capture=True)
            await publisher.await_fence()
            prepare_fixed_structural_lot_session=import_module(f"src.backend.backtest_fixed_structural_lot_execution_v{version}").prepare_fixed_structural_lot_session
            class SourceRows:
                def execute(self,sql,*args,**kwargs):return client.execute(sql,*args,**kwargs)
                def close(self):pass
            fresh_prepared=prepare_fixed_structural_lot_session(plans=plans,number=number,
                run_id=config.run_id,session_date=actual.operation.source.session_date,
                market=plans.market,candidates=plans.candidates,entry=plans.entry,seeds=plans.seeds,
                through_boundary_ms=57600000,client_factory=SourceRows)
            fresh_operation=fresh_prepared.operation
            assert fresh_operation.source is not actual.operation.source
            from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
            class ColdRows:
                fixed_structural_lot_profile=issue_fixed_structural_lot_profile(fresh_operation)
                backtest_v4_lease=None
                v4_batched_detail_readback=True
                def execute(self,sql,*args,**kwargs):return client.execute(sql,*args,**kwargs)
                def close(self):pass
            cold_client=ColdRows()
            from src.backend.backtest_fixed_structural_lot_resume import prepare_fixed_structural_lot_resume
            cold_resume=prepare_fixed_structural_lot_resume(cold_client,client.manager_keeper_session,
                prepared=fresh_prepared)
            cold_contexts=cold_resume.contexts
            cold_client.fixed_structural_lot_contexts=cold_contexts
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            cold_recoveries=[]
            cold_prefix=load_verified_v4_prefix(cold_client,config.run_id,
                first_price_source=fresh_operation.source.price_authority,
                fixed_lot_contexts=cold_contexts,_fixed_lot_cold_source=fresh_operation.source,
                _cold_recovery_context_sink=cold_recoveries)
            cold_client.fixed_lot_recovery_contexts=tuple(cold_recoveries)
            cold_image=cold_resume.image
            assert (cold_prefix.last_sequence,cold_prefix.last_batch_id)==(cold_image.sequence,cold_image.batch_id)
            # The old producer is quiescent. Continuation retains this exact
            # controlled run's current Keeper lease and typed dispatch owner.
            writer.close()
            cold_client.typed_insert_strict=True
            cold_client.typed_insert_dispatch=client.typed_insert_dispatch
            cold_client.backtest_v4_lease=client.backtest_v4_lease
            cold_client.manager_keeper_session=client.manager_keeper_session
            cold_journal=BacktestMemoryJournal(run_id=config.run_id,initial_sequence=cold_prefix.last_sequence)
            cold_writer=writer_module.ArteJournalWriter(cold_client,run_id=config.run_id,
                journal_profile='backtest_v4',coalesce_batches=False)
            cold_publisher=BacktestTypedJournalPublisher(cold_journal,cold_writer,attempt_id=str(uuid4()),
                run_month=actual.operation.source.session_date.replace(day=1),expected_config=flat,fixed_market_parent_plan=plans.market,
                initial_sequence=cold_prefix.last_sequence,prior_batch_id=cold_prefix.last_batch_id,
                source_cursor=cold_prefix.source_cursor)
            try:
                cold_publisher.bind_first_price_source(fresh_operation.source.price_authority)
                assert cold_publisher._first_price_source is fresh_operation.source.price_authority
                cold_publisher.restore_fixed_structural_lot_source(fresh_operation.source,
                    prefix=cold_prefix,contexts=cold_contexts)
                # Reuse the exact ordered production cold writer hydration:
                # committed campaign, admissions, protection records and OMS sources.
                from src.trading_runtime.selected_checkpoint_products import source_bound_oms_lineages
                from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
                from src.trading_runtime.strategy_one_campaign_snapshot import (
                    load_attested_campaign_snapshot,ManagedCampaignSnapshotHeadReader)
                cold_lineages=source_bound_oms_lineages(cold_client,cold_prefix,
                    source=fresh_operation.source,contexts=cold_contexts,
                    allowed_accounts=frozenset(config.account_ids))
                cold_campaign=load_attested_campaign_snapshot(cold_client,
                    ManagedCampaignSnapshotHeadReader(client.manager_keeper_session),run_id=config.run_id,
                    checkpoint_sequence=cold_image.sequence,fixed_lot_resume=cold_resume)
                cold_journal.restore_verified_campaign_ownership(cold_campaign)
                cold_journal.restore_verified_portfolio_admissions(cold_lineages)
                cold_history=load_complete_typed_protection_history(cold_client,cold_prefix,
                    fixed_lot_contexts=cold_contexts)
                cold_journal.restore_committed_records(cold_history.records)
                assert cold_journal.pending_record_count==0
                cold_publisher.restore_verified_oms_sources(cold_lineages,cold_history,
                    fixed_lot_resume=cold_resume)
                cold_owner=NativeFixedStructuralLotManagement(operation=fresh_operation,
                    publisher=cold_publisher,client=cold_client)
                from src.backend.backtest_fixed_journal_bootstrap import _fixed_structural_lot_oms_image
                cold_oms_image=_fixed_structural_lot_oms_image(cold_owner,cold_image,cold_contexts)
                assert cold_oms_image is not None
                from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
                from src.trading_runtime.strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
                from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
                from src.backend.backtest_v4_broker_quote_restore import CompletedBrokerQuote
                from src.backend.backtest_fixed_journal_bootstrap import restore_fixed_structural_lot_native_manager
                from src.trading_runtime.strategy_engine import StrategyAssignment
                recovered=recover_portfolio_engine_state(cold_client,run_id=config.run_id,
                    profiles=(profile,),state_revisions={entry.account_id:cold_image.sequence},cutoff_at=at)
                cold_portfolio=PortfolioManagementEngine((profile,),journal=cold_journal,
                    run_id=config.run_id,strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,
                    typed_recovery=recovered,event_clock=lambda:at)
                cold_broker=SimulatedBrokerAdapter(config.account_ids,SimulationConfig(initial_cash=10000.),
                    mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
                await cold_broker.initialize()
                broker_rows=load_unattested_broker_match_snapshot(cold_client,run_id=config.run_id,
                    checkpoint_sequence=cold_image.sequence)
                open_ids={v['broker_order_id'] for v in broker_rows.open_orders}
                requests_by_broker={identity:group.orders[group.broker_order_request_indexes[identity]]
                    for group in cold_oms_image.groups.values() for identity in group.broker_order_ids if identity in open_ids}
                assert set(requests_by_broker)==open_ids
                assert all(v['ticker']==entry.ticker and int(v['last_boundary_ms'])==partial_boundary
                    and int(v['quote_timestamp_us'])==partial_us for v in broker_rows.tickers if v['has_quote'])
                quotes={entry.ticker:CompletedBrokerQuote(entry.ticker,partial_boundary,partial_us,
                    partial_bid_int/10000,(partial_bid_int+100)/10000,4.,10000.)}
                cold_broker.restore_checkpoint_state(reconstruct_broker_match_state(broker_rows,
                    requests_by_broker_id=requests_by_broker,quotes=quotes))
                assignment=StrategyAssignment(entry.assignment_id,config.strategy_id,config.strategy_revision,
                    entry.account_id,entry.ticker,1,AssignmentStatus.MANAGING,StrategyPermissions(),{})
                cold_runtime=TradingRuntime(config,cold_broker,
                    SimpleNamespace(strategy_id=config.strategy_id,revision=config.strategy_revision,
                        automatic=True,assignments=lambda:(assignment,)),cold_journal,
                    portfolio=cold_portfolio,intent_planner=planner,typed_oms_image=cold_oms_image)
                fresh_operation.bind_runtime(cold_runtime)
                cold_runtime.last_event_time=at
                cold_evidence=ControlledCompletedEvidence();cold_evidence.boundary=partial_boundary
                cold_manager=StrategyOneManagementRunner(runtime=cold_runtime,evidence=cold_evidence,
                    tick_for_ticker=lambda ticker:.01)
                cold_manager.bind_fixed_structural_lot_management(cold_owner)
                before_cash=(await cold_broker.account_ledger(entry.account_id)).cashbalance
                before_next_order=cold_broker._next_order_id
                before_broker_bindings={identity:state.request for identity,state in cold_broker._orders.items()}
                before_requests={v.cOID for group in cold_runtime.order_manager._groups.values() for v in group.orders}
                restored_image=await restore_fixed_structural_lot_native_manager(cold_owner,cold_manager,
                    client.manager_keeper_session)
                key=(entry.account_id,entry.assignment_id,entry.ticker)
                assert restored_image.sequence==cold_image.sequence
                assert cold_owner.financials[key].position_quantity==partial_quantity
                assert sum(q for _,q in cold_owner.states[key].roster.remaining)==Decimal(str(quantity))
                assert (await cold_broker.account_ledger(entry.account_id)).cashbalance==before_cash
                assert cold_broker._next_order_id==before_next_order
                assert {identity:state.request for identity,state in cold_broker._orders.items()}==before_broker_bindings
                assert {v.cOID for group in cold_runtime.order_manager._groups.values() for v in group.orders}==before_requests
                assert cold_owner.states[key].protection.stop==owner.states[key].protection.stop
                await cold_runtime.initialize(record_lifecycle=False)
                completion_boundary=partial_boundary+100
                at=market_day_boundary(actual.operation.source.session_date,completion_boundary)
                completion_us=int(at.timestamp()*1000000)-1000
                complete_quote=dict(partial_quote,boundary_ms=completion_boundary,
                    bucket_index=partial_quote['bucket_index']+1,
                    first_event_us=completion_us,last_event_us=completion_us,quote_timestamp_us=completion_us,
                    bid_size=10000.,execution_volume=100000.,
                    execution_price_levels=({'price_int':partial_bid_int,'volume':100000.},))
                await cold_runtime.process_liquidity_boundary([complete_quote],at=at)
                assert cold_broker.position_quantity(entry.account_id,1,entry.ticker)==0
                completed_exit=next(group for group in cold_runtime.order_manager._groups.values() if group.intent.action=='exit')
                assert completed_exit.filled_quantity==quantity
                after_cash=(await cold_broker.account_ledger(entry.account_id)).cashbalance
                assert after_cash>before_cash
                assert completed_exit.intent.intent_id==exit_group.intent.intent_id
                await cold_publisher._drain(target_sequence=cold_journal.latest_sequence(config.run_id))
                await cold_runtime.finish('completed')
                terminal_sequence=cold_journal.latest_sequence(config.run_id)
                terminal=await cold_publisher.enqueue_terminal((cold_portfolio.capture_recovery_snapshot(
                    entry.account_id,state_revision=terminal_sequence,snapshot_at=at),))
                assert terminal.last_sequence==terminal_sequence and not cold_journal.unfenced_records()
                final_prefix=load_verified_v4_prefix(cold_client,config.run_id,
                    first_price_source=fresh_operation.source.price_authority,
                    fixed_lot_contexts=tuple(cold_client.fixed_structural_lot_contexts),
                    fixed_lot_recovery_contexts=tuple(cold_client.fixed_lot_recovery_contexts))
                assert final_prefix.status=='completed'
                print('actual_pending_profit_exit_partial_fresh_actor_full_exit_terminal='+str(terminal_sequence))
            finally:
                if 'cold_runtime' in locals():
                    if cold_runtime.order_manager is not None:await cold_runtime.order_manager.close()
                    for task in (cold_runtime._broker_stream_task,cold_runtime._risk_refresh_task):
                        if task is not None:
                            task.cancel()
                            try:await task
                            except asyncio.CancelledError:pass
                cold_writer.close();cold_journal.close()
            # Historical image is reconstructed from durable normalized rows,
            # never selected as a current execution head after completion.
            if strip_decimal:
                assert client.scale_stripped_cells > 0
            rebuilt=load_historical_checkpoint(cold_client,final_prefix,source=fresh_operation.source,
                sequence=references[0].checkpoint_sequence)
            assert rebuilt.inherited==image.inherited
            assert require_historical_checkpoint(rebuilt,source=fresh_operation.source) is rebuilt
            print('actual_selected_profit_exit_and_terminal_sequence='+str(terminal_sequence))

        finally:
            if runtime.order_manager is not None:await runtime.order_manager.close()
            for task in (runtime._broker_stream_task,runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            writer.close();journal.close()
    asyncio.run(exercise())




def test_execution_transport_applies_exact_v4_scope_before_limit():
    import json
    client=BatchedDecisionTransport()
    client.tables['trading_commit_v4']=[dict(run_id='r',batch_id='old',last_sequence=10),dict(run_id='r',batch_id='future',last_sequence=20)]
    details=[dict(run_id='r',batch_id=batch,broker_order_id=broker,execution_id=identity,
        record_id=identity,source_event_time='2026-08-04 08:00:00') for batch,broker,identity in
        (('old','2','foreign'),('future','1','future'),('old','1','owned'))]
    client.tables['trading_execution_v1']=details
    fence="AND batch_id IN (SELECT batch_id FROM arte.trading_commit_v4 WHERE run_id='r' AND last_sequence<=10) "
    query="SELECT execution_id FROM arte.trading_execution_v1 WHERE run_id='r' AND broker_order_id IN ('1') "+fence+"ORDER BY source_event_time,record_id LIMIT 1 FORMAT JSONEachRow"
    assert [json.loads(v)['execution_id'] for v in client.execute(query).splitlines()]==['owned']
    client.tables['trading_event_v1']=[dict(v,entity_id=v['execution_id'],category='execution',entity_type='fill',sequence=seq)
        for v,seq in zip(details,(2,20,3))]
    query="SELECT entity_id FROM arte.trading_event_v1 WHERE run_id='r' AND sequence>0 AND sequence<=10 AND category='execution' AND entity_type='fill' AND entity_id IN ('owned') "+fence+"ORDER BY sequence LIMIT 1 FORMAT JSONEachRow"
    assert [json.loads(v)['entity_id'] for v in client.execute(query).splitlines()]==['owned']
    with pytest.raises(AssertionError):client.execute(query.replace('trading_commit_v4','trading_commit_v3'))
    with pytest.raises(AssertionError):client.execute(query.replace('ORDER BY sequence','AND account_id=''foreign'' ORDER BY sequence'))
def test_real_oms_decoder_preserves_optional_binding_presence():
    from tests.test_arte_oms_actor_restore import _source,RUN,AT
    from src.trading_runtime.arte_oms_actor_restore import reconstruct_typed_oms_actor_image
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID,STRATEGY_NUMBER
    lineage,history=_source()
    for role,slice_present,filled in ((1,0,1),(0,1,0)):
        binding={**lineage.state.broker_bindings[0],'has_role':role,'role':'entry' if role else '',
            'has_slice':slice_present,'slice_id':'owned-slice' if slice_present else '',
            'has_filled_quantity':filled}
        actual_lineage=replace(lineage,state=replace(lineage.state,broker_bindings=(binding,)))
        image=reconstruct_typed_oms_actor_image((actual_lineage,),history,run_id=RUN,
            strategy_id=STRATEGY_ID,strategy_revision=STRATEGY_NUMBER,through_sequence=7,cutoff_at=AT)
        group=image.groups['group-1']
        assert group.broker_order_roles==({'broker-1':'entry'} if role else {})
        assert group.broker_order_slices==({'broker-1':'owned-slice'} if slice_present else {})
        assert group.filled_by_broker_order==({'broker-1':0.} if filled else {})
        assert group.broker_order_roles!={'broker-1':'foreign-role'}
        assert group.broker_order_slices!={'broker-1':'foreign-slice'}
def test_cold_product_read_adapter_transport_and_issued_scope_guards(monkeypatch):
    # Small wiring proof only: actual issued authority/recoveries are exercised by
    # the public-resume native case, not claimed by these controlled dependencies.
    from types import SimpleNamespace
    import src.trading_runtime.selected_checkpoint_products as products
    import src.trading_runtime.fixed_structural_lot_cold_recovery as cold
    import src.trading_runtime.fixed_structural_lot_profile as profiles
    source=SimpleNamespace(require_installed_admission=lambda:None)
    context=SimpleNamespace(source=source,verify_admission=lambda:None)
    prefix=SimpleNamespace(run_id='r',last_sequence=7,last_batch_id='b',source_cursor='c',status='running',batch_ids=('b',))
    profile=SimpleNamespace(operation=SimpleNamespace(source=source))
    reads=[]
    client=SimpleNamespace(fixed_structural_lot_profile=profile,v4_batched_detail_readback=True,
        execute=lambda sql:reads.append(sql) or 'owned')
    walk=SimpleNamespace(client=client,source=source,entries=(context,))
    monkeypatch.setattr(cold,'_walk_prefix',lambda value:prefix if value is walk else (_ for _ in ()).throw(ValueError('foreign walk')))
    monkeypatch.setattr(products,'selected',lambda value:value is source)
    monkeypatch.setattr(profiles,'require_fixed_structural_lot_profile',lambda value:profile if value is profile else (_ for _ in ()).throw(ValueError('foreign profile')))
    inventories=[]
    monkeypatch.setattr(products,'_read_recovery_inventory',lambda reader,actual_source,actual_prefix:inventories.append((reader.fixed_lot_recovery_contexts,actual_source,actual_prefix)))
    prior=(('b',object()),)
    reader=products.cold_checkpoint_product_reader(client,walk=walk,verified_prefix=prefix,recovery_contexts=prior)
    assert reader.execute('SELECT 1')=='owned'
    assert reads==['SELECT 1'] and inventories==[(prior,source,prefix),(prior,source,prefix)]
    assert reader.backtest_v4_lease is None and reader.typed_insert_dispatch is None
    for sql in ('INSERT INTO x VALUES (1)','WITH 1 SELECT 1'):
        with pytest.raises(ValueError):reader.execute(sql)
    with pytest.raises(ValueError):reader.execute('SELECT 1',parameters={})
    with pytest.raises(ValueError):products.cold_checkpoint_product_reader(client,walk=object(),verified_prefix=prefix)
    with pytest.raises(ValueError):products.cold_checkpoint_product_reader(client,walk=walk,verified_prefix=replace_dummy(prefix))
    prefix.source_cursor='mutated'
    with pytest.raises(ValueError):reader.execute('SELECT 1')
    assert reads==['SELECT 1']


def replace_dummy(value):
    from types import SimpleNamespace
    return SimpleNamespace(**vars(value))
def test_scoped_batched_product_envelope_preserves_legacy_and_exact_authority(monkeypatch):
    from types import SimpleNamespace
    import src.trading_runtime.selected_checkpoint_products as products
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix,_batched_detail_rows_v4
    from src.trading_runtime.strategy_registry import BATCHED_DETAIL_SELECT_RULE
    run='3fd8f092-3517-4dfc-b1f7-088cc05a8f11';batch='6ec4b06b-43ad-4ea6-a4dd-4a2c0ff3c746'
    rules=[BATCHED_DETAIL_SELECT_RULE]
    source=SimpleNamespace(run_id=run,require_installed_admission=lambda:None,
        installed_payload={'strategy':{'numbered_release':{'contract':{'rule_set_contracts':rules}}}})
    queries=[]
    client=SimpleNamespace(execute=lambda sql:queries.append(sql) or '',fixed_lot_recovery_contexts=())
    monkeypatch.setattr(products,'source_for_client',lambda value:source if value is client else None)
    prefix=V4CommittedPrefix(run,7,batch,'owned','running',(batch,))
    scope=products._SourceOmsReadScope(source,prefix,(),())
    products._OMS_READ_SCOPES[scope]=(client,source,prefix,(),(),products._read_prefix_identity(prefix))
    filters=f"WHERE run_id='{run}' AND batch_id=toUUID('{batch}') "
    specs=(('owned',('record_id',),0),)
    _batched_detail_rows_v4(client,specs,filters)
    assert queries[-1].startswith('(SELECT ')
    _batched_detail_rows_v4(client,specs,filters,fixed_lot_read_scope=scope)
    assert queries[-1].startswith('SELECT family_name,payload FROM (')
    baseline=len(queries)
    for invalid in (replace(scope),object()):
        with pytest.raises(ValueError):_batched_detail_rows_v4(client,specs,filters,fixed_lot_read_scope=invalid)
    with pytest.raises(ValueError):_batched_detail_rows_v4(SimpleNamespace(execute=client.execute),specs,filters,fixed_lot_read_scope=scope)
    for wrong in (filters.replace(run,'00000000-0000-0000-0000-000000000001'),filters.replace(batch,'00000000-0000-0000-0000-000000000001')):
        with pytest.raises(ValueError):_batched_detail_rows_v4(client,specs,wrong,fixed_lot_read_scope=scope)
    rules.append(BATCHED_DETAIL_SELECT_RULE)
    with pytest.raises(ValueError):_batched_detail_rows_v4(client,specs,filters,fixed_lot_read_scope=scope)
    rules.pop()
    source.price_authority=object()
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_commit_v4
    with pytest.raises(ValueError,match='substituted source context'):
        load_verified_commit_v4(client,run_id=run,batch_id=batch,first_price_source=source.price_authority,
            fixed_lot_read_scope=scope,fixed_lot_context=object())
    with pytest.raises(ValueError,match='foreign run'):
        load_verified_commit_v4(client,run_id='foreign',batch_id=batch,first_price_source=source.price_authority,fixed_lot_read_scope=scope)
    from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
    with pytest.raises(ValueError):
        load_committed_strategy_intent_page(client,prefix,include_source_batch=False,fixed_lot_read_scope=replace(scope),first_price_source=source.price_authority)
    object.__setattr__(scope,'contexts',(object(),))
    with pytest.raises(ValueError):_batched_detail_rows_v4(client,specs,filters,fixed_lot_read_scope=scope)
    assert len(queries)==baseline
def test_profit_reader_forwards_exact_nested_batch_scope_before_sealing(monkeypatch):
    # Controlled reader wiring; full seals and real issued authority are the
    # native public-resume acceptance gate.
    from types import SimpleNamespace
    import src.trading_runtime.arte_profit_giveback_reader_v4 as reader
    import src.trading_runtime.selected_checkpoint_products as products
    import src.backend.backtest_strategy_certified_price_break as prices
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    run='2b10d238-79e3-40c9-9322-f70992be9db9';batch='fe7c102d-6f27-4b1d-bca7-a891eb508aee'
    prefix=V4CommittedPrefix(run,7,batch,'cursor','running',(batch,))
    price=SimpleNamespace(run_id=run);scope=object();client=object();calls=[]
    monkeypatch.setattr(prices,'CertifiedPriceReadbackAuthority',SimpleNamespace)
    def validate(value,actual_client,actual_prefix):
        if value is not scope or actual_client is not client or actual_prefix is not prefix:raise ValueError('foreign scope')
        return SimpleNamespace(source=SimpleNamespace(price_authority=price))
    monkeypatch.setattr(products,'require_source_oms_read_scope',validate)
    monkeypatch.setattr(products,'source_batch_read_contexts',lambda value,c,p,b,authority:{'fixed_lot_read_scope':value} if (value,c,p,b,authority)==(scope,client,prefix,batch,price) else (_ for _ in ()).throw(ValueError('foreign batch')))
    monkeypatch.setattr(reader,'_rows',lambda c,sql:calls.append('read') or [dict(batch_id=batch)])
    monkeypatch.setattr(reader,'verified_batch_predecessor',lambda c,p,b:prefix)
    class Sealed(Exception):pass
    def whole_batch(c,**kwargs):
        assert c is client and kwargs['fixed_lot_read_scope'] is scope
        assert kwargs['batch_id']==batch and kwargs['first_price_source'] is price
        calls.append('complete-batch-verifier');raise Sealed()
    monkeypatch.setattr(reader,'load_verified_commit_v4',whole_batch)
    with pytest.raises(Sealed):reader.load_committed_profit_giveback(client,prefix,'93e25d29-1993-4a6b-81a8-e466af0c49ea',first_price_source=price,fixed_lot_read_scope=scope)
    assert calls==['read','complete-batch-verifier']
    with pytest.raises(ValueError):reader.load_committed_profit_giveback(client,prefix,'93e25d29-1993-4a6b-81a8-e466af0c49ea',first_price_source=price,fixed_lot_read_scope=object())
    assert calls==['read','complete-batch-verifier']
