from dataclasses import replace
from test_fixed_structural_lot_interval_validator_v2 import ordinal_transport_plan
def selected(monkeypatch, *, actual_loader=False):
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    from test_fixed_structural_lot_source_v2 import inputs
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    from src.backend import backtest_fixed_structural_lot_source_v6 as source
    from src.backend import backtest_fixed_structural_lot_source_v5 as scope_owner
    from src.backend import backtest_fixed_structural_lot_native_v6 as native
    from src.backend import backtest_fixed_structural_lot_native as owner
    from src.backend import backtest_fixed_structural_lot_execution_v6 as session
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.fixed_structural_lot_release_v6 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=84))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source if name == '_load_quotes' else scope_owner, name, getattr(previous, name))
    monkeypatch.setattr(scope_owner, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent, _, _, parent_release = declarations()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release, release=numbered_strategy(84), policy=old.policy.payload(), approved_code_commit=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(), approved_code_fingerprint=backend_source_fingerprint(), approval_reference='controlled immutable installation seam')['payload'])
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
    actual = session.prepare_fixed_structural_lot_session(plans=plans, number=84, run_id=old.run_id, session_date=old.session_date, market=plans.market, candidates=plans.candidates, entry=plans.entry, seeds=plans.seeds, through_boundary_ms=57600000, client_factory=Client)
    actual.require(market=plans.market, candidates=plans.candidates, entry=plans.entry, through_boundary_ms=57600000, run_id=old.run_id, number=84)
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
    from src.backend import backtest_fixed_structural_lot_native_v6 as native
    from src.trading_runtime.fixed_structural_lot_release_v6 import derive_fixed_structural_lot_release
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
    from src.backend import backtest_fixed_structural_lot_certification_v6 as seal
    native, parent, own = _actual_native_factory_declaration(monkeypatch, 84)
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        commit = os.environ['FIXED_LOT_PROPOSED_HEAD']
        real = subprocess.check_output
        monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
            (commit + '\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else real(args, **kwargs))
    if not seal.REVIEWED_SOURCE_AST:
        with pytest.raises(ValueError, match='unapproved'):
            native.load_installed_configuration(object(), number=84, parent=parent)
        return
    loaded, policy, proof = native.load_installed_configuration(object(), number=84, parent=parent)
    assert loaded is own and policy.payload() == actual_policy_payload()
    assert len(proof) == 64 and all(c in '0123456789abcdef' for c in proof)
    # No certification function, installed loader, or current-source verifier is replaced.
    assert native.verify_current_installed_source.__module__ == 'src.backend.backtest_fixed_structural_lot_native_v5'


def test_actual_native_factory_reaches_runtime_start_and_projection_without_certificate_stubs(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_certification_v6 as seal
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
                number=84, actual_factory_module='src.backend.backtest_fixed_structural_lot_native_v6',
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


def test_frozen83_actual_native_loader_reproduces_missing_import(monkeypatch):
    from test_fixed_structural_lot_native import cert, declarations
    from src.backend import backtest_fixed_structural_lot_native_v5 as frozen
    from src.trading_runtime.fixed_structural_lot_release_v5 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    parent, _, _, parent_release = declarations()
    own = cert(derive_fixed_structural_lot_release(parent, parent_release=parent_release,
        release=numbered_strategy(83), policy=actual_policy_payload(), approved_code_commit='a' * 40,
        approved_code_fingerprint=authority.backend_source_fingerprint(),
        approval_reference='controlled read seam reproducing frozen production failure')['payload'])
    monkeypatch.setattr(frozen, 'certify_numbered_configuration', lambda *_: own)
    with pytest.raises(ModuleNotFoundError, match='backtest_fixed_v5_certification'):
        frozen.load_installed_configuration(object(), number=83, parent=parent)


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
    from src.backend import backtest_fixed_structural_lot_native_v6 as native
    root = Path(native.__file__).resolve().parents[2]
    active = ('src/backend/backtest_fixed_structural_lot_native_v6.py',
        'src/backend/backtest_fixed_structural_lot_source_v6.py',
        'src/backend/backtest_fixed_structural_lot_execution_v6.py',
        'src/backend/backtest_fixed_structural_lot_empty_v6.py',
        'src/trading_runtime/fixed_structural_lot_release_v6.py',
        'src/trading_runtime/strategy_eighty_four_release.py',
        'src/trading_runtime/strategy_eighty_four_contract.py')
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


def test_real_hash_domains_and_saved_profile_binding(monkeypatch):
    actual, request, plans, config, client, flat = published(monkeypatch)
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.backend import backtest_fixed_structural_lot_saved_runtime_source as saved
    context = load_typed_run_context(client, config.run_id)
    payload = actual.operation.source.installed_payload
    runtime_hash = authority.backtest_code_hash(Path(authority.__file__).resolve().parents[2])
    backend_hash = authority.backend_source_fingerprint()
    assert runtime_hash != backend_hash
    assert context['code_hash'] == runtime_hash
    assert payload['strategy']['numbered_release']['approved_code_fingerprint'] == backend_hash
    release = SimpleNamespace(payload=payload, payload_hash=context['configuration_hash'])
    assert saved.require_saved_profile(actual.profile, context, release) is actual.profile
    client.fixed_structural_lot_profile = actual.profile
    assert saved.fixed_lot_saved_read_options(client, context, release) == {}
    for mode in ('paper', 'live', None):
        with pytest.raises(ValueError, match='run/session/configuration'):
            saved.require_saved_profile(actual.profile, {**context, 'mode': mode}, release)
    without_mode = {key: value for key, value in context.items() if key != 'mode'}
    with pytest.raises(ValueError, match='run/session/configuration'):
        saved.require_saved_profile(actual.profile, without_mode, release)
    for wrong in ('f' * 64, backend_hash):
        with pytest.raises(ValueError, match='approved execution source'):
            saved.require_saved_profile(actual.profile, {**context, 'code_hash': wrong}, release)
        from src.trading_runtime import arte_journal_writer as writer
        with monkeypatch.context() as scope:
            scope.setattr(writer, 'load_typed_run_context', lambda *_: {**context, 'code_hash': wrong})
            with pytest.raises(ValueError, match='published run/configuration/source'):
                authority.issue_fixed_lot_projection_authority(client, actual.operation.source, flat)


@pytest.mark.parametrize('field', ['approved_code_commit', 'approved_code_fingerprint'])
def test_installed_source_manifest_mutation_cannot_rebind_authority(monkeypatch, field):
    actual, request, plans, config, client, flat = published(monkeypatch)
    source = actual.operation.source
    payload = source.installed_payload
    payload['strategy']['numbered_release'][field] = 'f' * (40 if field.endswith('commit') else 64)
    object.__setattr__(source, 'installed_json', canonical_json(payload))
    with pytest.raises(ValueError):
        authority.issue_fixed_lot_projection_authority(client, source, flat)


@pytest.mark.parametrize('field', ['approved_code_commit', 'approved_code_fingerprint'])
def test_actual_source_preflight_rejects_wrong_commit_or_backend_hash(monkeypatch, field):
    from src.backend import backtest_fixed_structural_lot_native_v6 as native
    manifest = {'approved_code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
        'approved_code_fingerprint': authority.backend_source_fingerprint()}
    actual = subprocess.check_output
    # Dirty test checkout is the sole transport seam; hashes and HEAD are real.
    monkeypatch.setattr(subprocess, 'check_output', lambda args, **kw:
        b'' if args == ['git', 'status', '--porcelain'] else actual(args, **kw))
    own = SimpleNamespace(payload={'strategy': {'numbered_release': manifest}})
    native.verify_current_installed_source(own)
    manifest[field] = 'f' * (40 if field.endswith('commit') else 64)
    with pytest.raises(ValueError, match='exact clean approved executor'):
        native.verify_current_installed_source(own)


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
            from src.backend.backtest_fixed_structural_lot_saved_runtime_source import load_fixed_lot_saved_prefix
            payload = actual.operation.source.installed_payload
            saved_prefix = load_fixed_lot_saved_prefix(client, config.run_id, context,
                SimpleNamespace(payload=payload, payload_hash=context['configuration_hash']))
            assert saved_prefix.last_sequence == prefix.last_sequence
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
    from src.backend import backtest_fixed_structural_lot_certification_v6 as actual
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
    from src.backend import backtest_fixed_structural_lot_certification_v6 as certifier
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
                    if path not in required and path != 'src/backend/backtest_fixed_structural_lot_certification_v6.py':
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
            code_hash=authority.backtest_code_hash(Path(authority.__file__).resolve().parents[2]),market_plan_token=plans.market.token,started_at=request.intent.event_time)
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


@pytest.mark.parametrize('number',[77,80,81,82])
def test_unselected_projection_rule_delegates_identical_session_arguments(monkeypatch,number):
    from src.backend import backtest_fixed_structural_lot_execution_v6 as facade
    from src.backend import backtest_fixed_structural_lot_execution_v4 as previous
    arguments=dict(plans=object(),number=number,run_id=object(),session_date=object(),market=object(),
        candidates=object(),entry=object(),seeds=object(),through_boundary_ms=object(),client_factory=object())
    calls=[];result=object()
    monkeypatch.setattr(previous,'prepare_fixed_structural_lot_session',lambda **kw:calls.append(kw) or result)
    assert facade.prepare_fixed_structural_lot_session(**arguments) is result
    assert calls[0].keys()==arguments.keys()
    assert all(calls[0][key] is value for key,value in arguments.items())


def test_frozen_successor81_and82_and83_sources_and_seals_remain_exact():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    base = '90a793e3b5b6805a6759f8b4c7170d567a391de6'
    tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', base], cwd=root).decode().splitlines()
    frozen = [p for p in tracked if ('fixed_structural_lot' in p and p.endswith(('_v3.py', '_v4.py', '_v5.py')))
        or 'strategy_eighty_one_' in p or 'strategy_eighty_two_' in p or 'strategy_eighty_three_' in p
        or p in ('src/backend/backtest_fixed_structural_lot_projection_authority.py',
            'src/backend/backtest_fixed_structural_lot_saved_source.py',
            'src/backend/backtest_fixed_structural_lot_projection_runtime_authority.py',
            'src/backend/backtest_fixed_structural_lot_saved_runtime_source.py',
            'src/trading_runtime/fixed_structural_lot_interval_validator_v2.py')]
    assert len(frozen) >= 19
    for relative in frozen:
        baseline = subprocess.check_output(['git', 'show', base + ':' + relative], cwd=root).decode('utf-8')
        assert (root / relative).read_text(encoding='utf-8') == baseline


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
