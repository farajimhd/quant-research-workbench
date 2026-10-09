"""Actual native metadata and complete immutable99 compiler inheritance."""
from copy import deepcopy
from dataclasses import fields, replace
from hashlib import sha256
from pathlib import Path
import ast
import json

import pytest

from src.trading_runtime.strategy_ninety_nine_contract import strategy_ninety_nine_contract
from src.trading_runtime.strategy_ninety_nine_release import derive_strategy_ninety_nine_configuration
from src.trading_runtime.strategy_one_hundred_one_contract import strategy_one_hundred_one_contract
from src.trading_runtime.strategy_one_hundred_one_release import (
    release_contract, derive_strategy_one_hundred_one_configuration,
    verify_prepared_strategy_one_hundred_one_configuration,
)
from src.trading_runtime.fixed_lot_management_reuse_policy import INPUT, RULE
from src.trading_runtime.declared_native_manifest import registered_manifest_authority
from src.trading_runtime.strategy_registry import numbered_strategy, numbered_strategy_parent
from src.backend.backtest_fixed_structural_lot_configuration import (
    verify_fixed_structural_lot_configuration, derive_registered_fixed_structural_lot_configuration,
)
from test_fixed_structural_lot_configuration_routing import source_fixture
from test_strategy_fifty_release import APPROVAL
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope, publish_configuration


def prepared():
    return derive_strategy_one_hundred_one_configuration(source_fixture(), **APPROVAL)


def differences(left, right, prefix=()):
    if type(left) is dict and type(right) is dict:
        return set().union(*(differences(left.get(k), right.get(k), (*prefix, k))
                             for k in set(left) | set(right)))
    return set() if left == right else {prefix}


def test_exact_factory_and_release_preserve_all99_trading_fields():
    prior, current = strategy_ninety_nine_contract(), strategy_one_hundred_one_contract()
    for field in fields(prior):
        if field.name not in {'strategy_number', 'release', 'management_reuse_policy'}:
            assert getattr(current, field.name) == getattr(prior, field.name)
    assert prior.management_reuse_policy is None
    assert current.release.input_contracts == (*prior.release.input_contracts, INPUT)
    assert current.release.rule_set_contracts == (*prior.release.rule_set_contracts, RULE)
    assert numbered_strategy(101) == current.release == release_contract()
    assert numbered_strategy_parent(101) == 42
    assert registered_manifest_authority(101).source_prefix == 'fixed-structural-lots-from'
    with pytest.raises(ValueError, match='paired'):
        replace(current, management_reuse_policy=None)


def test_complete_compiled_tree_has_only_declared_successor_differences():
    parent = source_fixture()
    prior = derive_strategy_ninety_nine_configuration(parent, **APPROVAL)['payload']
    current = prepared()['payload']
    allowed = {
        ('strategy', 'strategy_number'), ('strategy', 'revision'), ('strategy', 'profile_id'),
        ('strategy', 'profile_revision'), ('strategy', 'name'),
        ('strategy', 'parameters', 'management_reuse_policy'),
        ('strategy', 'numbered_release', 'contract', 'number'),
        ('strategy', 'numbered_release', 'contract', 'executor_revision'),
        ('strategy', 'numbered_release', 'contract', 'input_contracts'),
        ('strategy', 'numbered_release', 'contract', 'rule_set_contracts'),
        ('strategy', 'numbered_release', 'contract', 'behavior_specification'),
        ('strategy', 'numbered_release', 'approved_digest'),
        ('strategy', 'numbered_release', 'manifest_hash'),
        ('strategy_profile', 'profile_id'), ('strategy_profile', 'revision'),
        ('strategy_profile', 'definition_revision'), ('strategy_profile', 'name'),
        ('strategy_profile', 'description'), ('run_plan', 'profile_id'),
        ('run_plan', 'name'), ('run_plan', 'description'),
    }
    assert differences(prior, current) == allowed
    assert current['strategy']['parameters']['fixed_structural_lot_parent'] == prior['strategy']['parameters']['fixed_structural_lot_parent']
    assert verify_prepared_strategy_one_hundred_one_configuration(parent, current)['payload'] == current
    assert verify_fixed_structural_lot_configuration(current['strategy']) == strategy_one_hundred_one_contract()
    assert derive_registered_fixed_structural_lot_configuration(parent, number=101, **APPROVAL) == prepared()
    assert _verified_numbered_envelope({k: v for k, v in prepared().items() if k != 'nodes'})[0] == current


@pytest.mark.parametrize('path', [
    ('strategy', 'parameters', 'management_reuse_policy', 'max_entries'),
    ('strategy', 'parameters', 'execution'),
    ('strategy', 'parameters', 'sizing'),
    ('strategy', 'parameters', 'fixed_structural_lot_parent', 'payload_hash'),
    ('strategy', 'numbered_release', 'source_payload_hash'),
    ('run_plan', 'name'),
])
def test_complete_rederivation_rejects_changed_policy_economics_and_identity(path):
    payload = deepcopy(prepared()['payload'])
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = 31 if path[-1] == 'max_entries' else 'changed'
    with pytest.raises(ValueError):
        verify_prepared_strategy_one_hundred_one_configuration(source_fixture(), payload)


def test_catalog_cannot_approve_unreviewed_current_source(monkeypatch):
    authority = registered_manifest_authority(101)
    original = Path.read_text
    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'fixed_lot_management_reuse_policy.py':
            source += '\nUNREVIEWED_SOURCE_CHANGE = True\n'
        return source
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='inventory|unapproved|reviewed source'):
        authority.certify_source()


def test_publisher_rejects_malformed_envelope_before_any_write():
    envelope = {k: v for k, v in prepared().items() if k != 'nodes'}
    envelope['source_candidate_id'] = 'foreign-parent:' + envelope['source_candidate_id'].split(':', 1)[1]
    class Writer:
        def execute(self, *args, **kwargs):
            pytest.fail('Malformed native envelope reached a write client')
    with pytest.raises(ValueError):
        publish_configuration(Writer(), object(), envelope)


def test_registered_transport_reaches_official_publisher_and_normalized_reader(monkeypatch):
    """Only SQL parent transport is synthetic; inheritance uses actual adapters."""
    import pipelines.strategy_one.configuration_publisher as publisher
    import src.backend.backtest_strategy_one_configuration as reader
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes
    parent = source_fixture()
    envelope = prepared()
    assert set(envelope) == {'source_candidate_id', 'source_candidate_hash',
        'payload_hash', 'node_hash', 'node_count', 'payload'}
    internal = verify_prepared_strategy_one_hundred_one_configuration(parent, envelope['payload'])
    assert internal['nodes'] == encode_nodes(envelope['payload'])
    assert {key: internal[key] for key in envelope} == envelope
    monkeypatch.setattr(publisher, 'certify_numbered_configuration',
        lambda client, number: parent if number == 42 else pytest.fail('Foreign parent'))
    def before_layout(client):
        raise RuntimeError('Verified complete inheritance; stop before inserts')
    monkeypatch.setattr(publisher, 'verify_tables', before_layout)
    with pytest.raises(publisher.PublicationStageError) as error:
        publisher.publish_configuration(object(), object(), envelope)
    assert str(error.value.__cause__) == 'Verified complete inheritance; stop before inserts'

    attempt = '00000000-0000-0000-0000-000000000101'
    class ReaderTransport:
        def execute(self, query):
            assert query.startswith('SELECT') and 'strategy_number=101' in query
            if 'release_attempt_id,strategy_id' in query:
                return json.dumps(dict(release_attempt_id=attempt,
                    strategy_id=envelope['payload']['strategy']['strategy_id'],
                    **{key: envelope[key] for key in ('source_candidate_id',
                        'source_candidate_hash', 'payload_hash', 'node_count', 'node_hash')}))
            return '\n'.join(json.dumps(row) for row in internal['nodes'])
    original = reader.certify_numbered_configuration
    def parent_transport(client, number=1):
        return parent if number == 42 else original(client, number)
    monkeypatch.setattr(reader, 'certify_numbered_configuration', parent_transport)
    certified = original(ReaderTransport(), 101)
    assert certified.payload == envelope['payload'] and certified.attempt_id == attempt
    assert certified.payload_hash == envelope['payload_hash']
    assert certified.node_hash == envelope['node_hash']


@pytest.mark.parametrize('relative', [
    'src/backend/backtest_fixed_structural_lot_source.py',
    'src/backend/backtest_fixed_structural_lot_management.py',
    'src/trading_runtime/strategy_registry.py',
])
def test_exact_compatibility_preserves99_and_rejects_unsupported_drift(relative):
    from src.backend import backtest_fixed_structural_lot_compatibility_v20 as current
    from src.backend import backtest_declared_waiting_ladder_compatibility as waiting
    from src.backend.backtest_fixed_structural_lot_certification_v19 import REVIEWED_SOURCE_AST
    source = (Path(current.__file__).parents[2] / relative).read_text(encoding='utf-8')
    restored = waiting.restore_reviewed_parent_source(
        current.restore_reviewed_parent_source(source, relative), relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == REVIEWED_SOURCE_AST[relative]
    changed = source + '\nUNREVIEWED_SEMANTIC_CHANGE = True\n'
    assert current.restore_reviewed_parent_source(changed, relative) == changed


def test_compatibility_own_envelope_and_loaded_metadata_are_sealed(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_compatibility_v20 as current
    original = Path.read_text
    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        return source + '\nFOREIGN_ENVELOPE = True\n' if path == Path(current.__file__) else source
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', changed)
        with pytest.raises(ValueError, match='envelope'):
            current.restore_reviewed_parent_source('x = 1', 'unknown.py')
    monkeypatch.setattr(current, 'APPROVED_METADATA_ANCHOR', '0' * 64)
    with pytest.raises(ValueError, match='loaded and fresh'):
        current.restore_reviewed_parent_source('x = 1', 'unknown.py')


@pytest.mark.parametrize('relative', ['unknown.py', 'src/trading_runtime/strategy_registry.py'])
def test_compatibility_early_returns_reread_own_source(monkeypatch, relative):
    from src.backend import backtest_fixed_structural_lot_compatibility_v20 as current
    original = Path.read_text
    reads = 0
    def changed(path, *args, **kwargs):
        nonlocal reads
        source = original(path, *args, **kwargs)
        if path == Path(current.__file__):
            reads += 1
            if reads > 1:
                return source + '\n# changed during restore\n'
        return source
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='changed during restoration'):
        current.restore_reviewed_parent_source('x = 1', relative)


def test_retained97_composition_binds_current_and_retained_source():
    from src.backend.backtest_fixed_structural_lot_compatibility_v20 import certify_retained_waiting_ladder_source
    assert registered_manifest_authority(97).certify_source is certify_retained_waiting_ladder_source
    assert len(certify_retained_waiting_ladder_source()) == 64


def test_fresh97_pin_mutation_rejects_even_with_recomputed_metadata(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_compatibility_v20 as current
    prior = Path(current.__file__).with_name('backtest_declared_waiting_ladder_certification.py')
    source = prior.read_text(encoding='utf-8')
    tree = ast.parse(source)
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if type(n) is ast.Assign}
    pins = dict(values['REVIEWED_SOURCE_AST'])
    pins[next(key for key, value in pins.items() if type(value) is str)] = '0' * 64
    anchor = sha256(json.dumps(dict(required=values['REQUIRED_SOURCE_FILES'], sources=pins,
        review_origins=values['SOURCE_REVIEW_ORIGINS'], pending=values['PENDING_SOURCE_REVIEWS']),
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    replacements = {'REVIEWED_SOURCE_AST': pins, 'APPROVED_METADATA_ANCHOR': anchor}
    lines = source.splitlines(keepends=True)
    for node in reversed(tree.body):
        if type(node) is ast.Assign and node.targets[0].id in replacements:
            name = node.targets[0].id
            lines[node.lineno - 1:node.end_lineno] = [f'{name} = {replacements[name]!r}\n']
    changed_source = ''.join(lines)
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *a, **k:
        changed_source if path == prior else original(path, *a, **k))
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        current.certify_retained_waiting_ladder_source()


def test_composition_final_reread_detects_post_current_proof_change(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_compatibility_v20 as compatibility
    from src.backend import backtest_fixed_structural_lot_certification_v20 as current
    original_proof, original_read = current.certify_fixed_structural_lot_source, Path.read_text
    finished = False
    def proof():
        nonlocal finished
        result = original_proof()
        finished = True
        return result
    def changed(path, *args, **kwargs):
        source = original_read(path, *args, **kwargs)
        if finished and path.name == 'fixed_lot_management_reuse_policy.py':
            return source + '\n# changed after current proof\n'
        return source
    monkeypatch.setattr(current, 'certify_fixed_structural_lot_source', proof)
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='changed during composition'):
        compatibility.certify_retained_waiting_ladder_source()
