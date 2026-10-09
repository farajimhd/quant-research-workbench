"""Installed native preparation contracts; controlled transport is explicit."""
from copy import deepcopy
from dataclasses import replace, fields
from hashlib import sha256
from uuid import uuid4
import json
import pytest

from test_fixed_structural_lot_configuration_routing import source_fixture
from test_strategy_fifty_release import APPROVAL
from src.trading_runtime.strategy_registry import numbered_strategy
from src.trading_runtime.strategy_one_hundred_one_contract import strategy_one_hundred_one_contract
from src.trading_runtime.strategy_one_hundred_two_contract import strategy_one_hundred_two_contract
from src.trading_runtime.strategy_one_hundred_one_release import derive_strategy_one_hundred_one_configuration
from src.trading_runtime.strategy_one_hundred_two_release import (
    derive_strategy_one_hundred_two_configuration, verify_prepared_strategy_one_hundred_two_configuration,
)
from src.trading_runtime.fixed_lot_management_native_preparation import INPUT, RULE, uses_management_native_preparation
from src.backend.backtest_fixed_structural_lot_native_v20 import verify_installed_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json


def certified(envelope):
    attempt = str(uuid4())
    number = envelope['payload']['strategy']['strategy_number']
    return CertifiedStrategyOneConfiguration(attempt, envelope['payload_hash'], envelope['node_hash'],
        envelope['source_candidate_id'], envelope['source_candidate_hash'],
        sha256(canonical_json((number, attempt, envelope['payload_hash'], envelope['node_hash'])).encode()).hexdigest(),
        envelope['payload'])


def changed_release(release, **changes):
    value = replace(release, approved_digest='', **changes)
    return replace(value, approved_digest=value.digest())


def test_complete101_tree_and_factory_preserved_except_declared_preparation_identity():
    parent = source_fixture()
    prior = derive_strategy_one_hundred_one_configuration(parent, **APPROVAL)
    current = derive_strategy_one_hundred_two_configuration(parent, **APPROVAL)
    assert set(prior) == set(current) == {'source_candidate_id', 'source_candidate_hash',
        'payload', 'payload_hash', 'node_hash', 'node_count'}
    assert current['payload']['strategy']['parameters'] == prior['payload']['strategy']['parameters']
    restored = deepcopy(current['payload'])
    for section, keys in {
        'strategy': ('strategy_number', 'revision', 'profile_id', 'profile_revision', 'name', 'numbered_release'),
        'strategy_profile': ('profile_id', 'revision', 'definition_revision', 'name', 'description'),
        'run_plan': ('profile_id', 'name', 'description'),
    }.items():
        for key in keys:
            restored[section][key] = deepcopy(prior['payload'][section][key])
    assert restored == prior['payload']
    previous, own = strategy_one_hundred_one_contract(), strategy_one_hundred_two_contract()
    for field in fields(previous):
        if field.name not in ('strategy_number', 'release'):
            assert getattr(own, field.name) == getattr(previous, field.name)
    assert own.release.input_contracts == (*previous.release.input_contracts, INPUT)
    assert own.release.rule_set_contracts == (*previous.release.rule_set_contracts, RULE)
    assert verify_prepared_strategy_one_hundred_two_configuration(parent, current['payload']) == current
    assert verify_installed_configuration(parent, certified(current), own.release, numbered_strategy(42)) == own.fixed_structural_lot_policy


@pytest.mark.parametrize('change', ['missing_input', 'missing_rule', 'duplicate_input', 'duplicate_rule'])
def test_preparation_markers_fail_closed(change):
    release = numbered_strategy(102)
    inputs, rules = release.input_contracts, release.rule_set_contracts
    if change == 'missing_input': inputs = tuple(x for x in inputs if x != INPUT)
    if change == 'missing_rule': rules = tuple(x for x in rules if x != RULE)
    if change == 'duplicate_input': inputs = (*inputs, INPUT)
    if change == 'duplicate_rule': rules = (*rules, RULE)
    expected = 'approved seal' if change.startswith('duplicate') else 'paired'
    with pytest.raises(ValueError, match=expected):
        uses_management_native_preparation(changed_release(release, input_contracts=inputs, rule_set_contracts=rules))


def test_unselected101_keeps_original_native_preparation_route(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_execution_v20 as selected
    calls = []
    monkeypatch.setattr(selected.legacy, 'prepare_fixed_structural_lot_session',
        lambda **kwargs: calls.append(kwargs) or 'original-v19')
    assert not uses_management_native_preparation(numbered_strategy(101))
    assert selected.prepare_fixed_structural_lot_session(plans=None, number=101,
        run_id='unchanged', session_date=None, market=None, candidates=None,
        entry=None, seeds=None, through_boundary_ms=100, client_factory=None) == 'original-v19'
    assert calls[0]['number'] == 101


@pytest.mark.parametrize('path', [
    ('strategy', 'parameters', 'management_reuse_policy', 'max_entries'),
    ('strategy', 'parameters', 'execution', 'tick_size'),
    ('strategy', 'numbered_release', 'source_payload_hash'),
])
def test_installed_loader_manifest_rederivation_rejects_resealed_drift(path):
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
    parent = source_fixture()
    envelope = derive_strategy_one_hundred_two_configuration(parent, **APPROVAL)
    payload = envelope['payload']
    target = payload
    for key in path[:-1]: target = target[key]
    target[path[-1]] = 31 if path[-1] == 'max_entries' else .02 if path[-1] == 'tick_size' else 'f' * 64
    envelope.update(payload_hash=sha256(canonical_json(payload).encode()).hexdigest(), node_hash=node_hash(encode_nodes(payload)))
    with pytest.raises(ValueError):
        verify_installed_configuration(parent, certified(envelope), numbered_strategy(102), numbered_strategy(42))


def exact_parent():
    from pathlib import Path
    p = Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy101-exact42-parent-configuration-v1.json')
    assert sha256(p.read_bytes()).hexdigest() == '8791154cd6b2ade7a04d5f31e7def8e666ea53ec17ff50084111144afa2178f9'
    return CertifiedStrategyOneConfiguration(**json.loads(p.read_text(encoding='utf-8'))['certificate'])


def current_approval():
    import subprocess
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    return dict(approved_code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
        approved_code_fingerprint=backend_source_fingerprint(),
        approval_reference='Controlled immutable configuration transport. Actual installed loader and complete source proof; no actual market or financial claim.')


def controlled_clean_status(monkeypatch):
    # A mutable precommit test fixture cannot be a clean execution deployment.
    # Only Git's clean-status transport is controlled; commit, source fingerprints,
    # actual AST certificate, installed manifest/factory and source loader remain real.
    import subprocess
    original = subprocess.check_output
    monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
        b'' if args == ['git', 'status', '--porcelain'] else original(args, **kwargs))


def prepared_nonempty_session(monkeypatch, *, resume=False):
    number, version = 102, 20
    from test_fixed_structural_lot_checkpoint_reader_profile import ordinal_transport_plan, controlled_price_rows
    from importlib import import_module
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    from test_fixed_structural_lot_source_v2 import inputs
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    source = import_module(f'src.backend.backtest_fixed_structural_lot_source_v{version}')
    from src.backend import backtest_fixed_structural_lot_source_v5 as scope_owner
    native = import_module(f'src.backend.backtest_fixed_structural_lot_native_v{version}')
    from src.backend import backtest_fixed_structural_lot_native as owner
    session = import_module(f'src.backend.backtest_fixed_structural_lot_execution_v{version}')
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=number))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source if name == '_load_quotes' else scope_owner, name, getattr(previous, name))
    monkeypatch.setattr(scope_owner, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent = exact_parent()
    own = certified(derive_strategy_one_hundred_two_configuration(parent, **current_approval()))
    from src.backend import backtest_fixed_structural_lot_native as source_issuer
    controlled_clean_status(monkeypatch)
    monkeypatch.setattr(source, 'certify_numbered_configuration', lambda client, selected: parent if selected == 42 else own)
    monkeypatch.setattr(native, 'certify_numbered_configuration', lambda client, selected: own if selected == number else parent)
    complete_selected = True
    monkeypatch.setattr(execution, 'prepare_strategy_one_entry_authorities', lambda **kw: (plans.candidates, authority.entry_activity_source.gate, None, None, None, (), authority))

    class Client:

        def close(self):
            calls.append(('closed',))
        def execute(self, query):
            # This is synthetic producer transport for owner/recovery tests,
            # not proof of actual market coverage. The price verifier is real.
            if not complete_selected:
                raise AssertionError('Legacy fixture unexpectedly queried price transport')
            return controlled_price_rows(plans, query)
    if complete_selected:
        from src.backend.backtest_liquidity_price import certify_price_level_plan
        plans = replace(plans, prices=certify_price_level_plan(plans.execution_market, Client()))
    if resume:
        # Execute the actual replay resume preparation block, including its
        # asyncio.to_thread boundary. Plan transport is controlled here; journal
        # recovery and clean deployment are separate qualification gates.
        import ast
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace
        from src.backend import backtest_market_data
        tree = ast.parse((Path(__file__).parents[1] / 'src/backend/replay_run_service.py').read_text(encoding='utf-8'))
        blocks = [node for node in ast.walk(tree) if isinstance(node, ast.If)
            and any(isinstance(child, ast.ImportFrom)
                and child.module == 'backtest_fixed_structural_lot_execution_v20'
                for child in node.body)]
        assert len(blocks) == 2
        block = max(blocks, key=lambda node: node.lineno)
        wrapper = ast.AsyncFunctionDef(name='resume_preparation',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[block, ast.Return(value=ast.Name(id='selected_lot_session', ctx=ast.Load()))],
            decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        monkeypatch.setattr(backtest_market_data, 'readonly_clickhouse_client', lambda **kwargs: Client())
        from src.backend.backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract
        namespace = dict(__package__='src.backend', asyncio=asyncio,
            declared_fixed_structural_lot_contract=declared_fixed_structural_lot_contract,
            strategy_number=number, run_id=old.run_id, plans=plans,
            definition=SimpleNamespace(session_date=old.session_date),
            controller=SimpleNamespace(_fixed_through_boundary_ms=lambda: 57600000))
        exec(compile(module, '<actual replay resume preparation block>', 'exec'), namespace)
        actual = asyncio.run(namespace['resume_preparation']())
    else:
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


def test_actual_complete_nonempty_session_uses_real_installed_loader_and_source_proof(monkeypatch):
    """Actual public prep/loader/profile/request; controlled market and SQL fixtures."""
    from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_profile
    actual, request, plans = prepared_nonempty_session(monkeypatch)
    assert actual.operation.source.installed_payload['strategy']['strategy_number'] == 102
    assert actual.operation.source.policy == strategy_one_hundred_two_contract().fixed_structural_lot_policy
    assert require_fixed_structural_lot_profile(actual.profile).operation is actual.operation
    assert request.revision == 102
    request.verify()
    with pytest.raises(ValueError, match='factory-issued'):
        replace(actual)._require_issued()


def test_actual_positive_empty_session_uses_real_installed_loader_and_source_proof(monkeypatch):
    from datetime import date
    from src.backend import backtest_market_data as market_api
    from src.backend import backtest_input_scope as scope_api
    from src.backend import backtest_fixed_structural_lot_native_v20 as native
    from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan, RULE_DIGEST
    from src.backend.backtest_strategy_one_plan import StrategyOneFixedPlans
    from src.backend.backtest_fixed_structural_lot_execution_v20 import prepare_fixed_structural_lot_session
    from test_fixed_structural_lot_empty import EmptyReader
    from test_backtest_strategy_one_candidate_store import _market, THROUGH
    parent = exact_parent()
    own = certified(derive_strategy_one_hundred_two_configuration(parent, **current_approval()))
    controlled_clean_status(monkeypatch)
    monkeypatch.setattr(native, 'certify_numbered_configuration',
        lambda client, number: parent if number == 42 else own)
    verified = []
    monkeypatch.setattr(market_api, 'verify_market_day_plan',
        lambda market, **kwargs: verified.append(market.tickers))
    monkeypatch.setattr(scope_api, 'input_exclusions', lambda session: ())
    class Reader(EmptyReader):
        def close(self): pass
    reader = Reader()
    market = _market()
    candidates = certify_candidate_plan(market, candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=THROUGH, client=reader)
    execution = market_api.project_empty_market_day_plan(market, empty_candidate_token=candidates.token)
    plans = StrategyOneFixedPlans(market, object(), execution, None, candidates,
        None, None, None, None, None, None)
    run = str(uuid4())
    actual = prepare_fixed_structural_lot_session(plans=plans, number=102, run_id=run,
        session_date=date.fromisoformat(market.sessions[0]), market=market,
        candidates=candidates, entry=None, seeds=None, through_boundary_ms=THROUGH,
        client_factory=Reader)
    assert verified == [market.tickers]
    assert actual.operation.source.installed_payload['strategy']['strategy_number'] == 102
    assert not actual.operation.source.candidates.prepared
    actual.require(market=market, candidates=candidates, entry=None,
        through_boundary_ms=THROUGH, run_id=run, number=102)
    with pytest.raises(ValueError, match='grants no entry'):
        actual.operation.source.request(object())


def test_initial_and_resume_call_sites_use_same_generic_preparation_dispatcher():
    import ast
    from pathlib import Path
    source = Path(__file__).parents[1] / 'src/backend/replay_run_service.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and node.module == 'backtest_fixed_structural_lot_execution_v20']
    assert len(imports) == 2
    assert all([(name.name, name.asname) for name in node.names]
        == [('prepare_fixed_structural_lot_session', None)] for node in imports)


def test_actual_async_resume_preparation_issues_and_verifies_nonempty_session(monkeypatch):
    actual, request, plans = prepared_nonempty_session(monkeypatch, resume=True)
    assert actual.operation.source.installed_payload['strategy']['strategy_number'] == 102
    actual._require_issued()
    request.verify()
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_native as issuer
    from src.backend import backtest_fixed_structural_lot_certification_v21 as certificate
    root = Path(__file__).parents[1]
    snapshot = {relative: sha256((root / relative).read_text(encoding='utf-8').encode()).hexdigest()
        for relative in certificate.REQUIRED_SOURCE_FILES}
    packet = dict(status='passed', recursive_proof=issuer._INSTALLED_SOURCES[actual.operation.source][-1],
        current_source_seal=certificate.certify_fixed_structural_lot_source(),
        source_snapshot=snapshot,
        source_snapshot_hash=sha256(canonical_json(snapshot).encode()).hexdigest(),
        seams=['controlled market/entry/SQL transport', 'precommit git-clean transport',
            'actual replay resume preparation block; not full journal resume assembly'],
        financial_qualification=False, clean_deployment_qualification=False)
    Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy102-actual-resume-source-proof-v1.json').write_text(
        json.dumps(packet, indent=2, sort_keys=True), encoding='utf-8')


@pytest.mark.parametrize('leaf', [
    'backtest_fixed_structural_lot_native_v20.py',
    'backtest_fixed_structural_lot_empty_v20.py',
    'backtest_fixed_v4_certification.py',
    'backtest_fixed_structural_lot_certification_v20.py',
])
def test_current_and_retained_source_drift_rejected(monkeypatch, leaf):
    from pathlib import Path
    from src.backend.backtest_fixed_structural_lot_certification_v21 import certify_fixed_structural_lot_source
    original = Path.read_text
    def read(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text + '\nunapproved_module_state = True\n' if path.name == leaf else text
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError):
        certify_fixed_structural_lot_source()


def test_compatibility_helper_envelope_and_source_race_rejected(monkeypatch):
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_compatibility_v21 as compatibility
    original = Path.read_text
    def changed(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text + '\nunapproved_module_state = True\n' if path.name == Path(compatibility.__file__).name else text
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'read_text', changed)
        with pytest.raises(ValueError, match='envelope differs'):
            compatibility.restore_reviewed_parent_source('', 'unknown.py')
    reads = 0
    def raced(path, *args, **kwargs):
        nonlocal reads
        text = original(path, *args, **kwargs)
        if path.name == Path(compatibility.__file__).name:
            reads += 1
            if reads > 1: return text + '\n# changed during verification\n'
        return text
    monkeypatch.setattr(Path, 'read_text', raced)
    with pytest.raises(ValueError, match='changed during restoration'):
        compatibility.restore_reviewed_parent_source('', 'unknown.py')


