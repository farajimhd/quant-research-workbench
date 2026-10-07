"""Real registered routing with prepared parent transport; no installed/source claims."""
from copy import deepcopy
from hashlib import sha256
import json
import pytest
from test_strategy_sixty_six_release import source_fixture as declared_parent_fixture
from test_strategy_fifty_release import APPROVAL
from src.backend import backtest_fixed_structural_lot_configuration as selected
from src.backend import backtest_strategy_one_configuration as reader
from src.trading_runtime.strategy_registry import numbered_strategy_parent
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from pipelines.strategy_one import configuration_publisher as publisher


def source_fixture():
    # Recompute prepared content seal; this is not the pinned DB parent certificate.
    from dataclasses import replace
    parent = declared_parent_fixture()
    digest = sha256(canonical_json(parent.payload).encode()).hexdigest()
    token = sha256(canonical_json((42,parent.attempt_id,digest,parent.node_hash)).encode()).hexdigest()
    return replace(parent, payload_hash=digest, token=token)


def envelope():
    result = selected.derive_registered_fixed_structural_lot_configuration(source_fixture(),number=77,**APPROVAL)
    return {k:v for k,v in result.items() if k != 'nodes'}


def test_real_registered_factory_and_prepared_payload():
    result = envelope()
    assert numbered_strategy_parent(77) == 42
    assert selected.declared_fixed_structural_lot_contract(77).fixed_structural_lot_policy.count == 3
    assert reader.is_numbered_fixed_configuration(result['payload'])
    reader._validate_strategy_two_payload(result['payload'])
    payload,nodes = publisher._verified_numbered_envelope(result)
    assert payload == result['payload']
    assert node_hash(nodes) == result['node_hash']


@pytest.mark.parametrize('change',['count','missing_policy','extra','manifest','prefix'])
def test_foreign_selected_envelope_rejected(change):
    result = envelope()
    params=result['payload']['strategy']['parameters']
    if change=='count': params['fixed_structural_lot_policy']['count']=4
    elif change=='missing_policy':params.pop('fixed_structural_lot_policy')
    elif change=='extra':params['caller_approved']=True
    elif change=='manifest':result['payload']['strategy']['numbered_release']['approved_digest']='f'*64
    else:result['source_candidate_id']='strategy-seventy-seven-from:foreign'
    nodes=encode_nodes(result['payload'])
    result.update(payload_hash=sha256(canonical_json(result['payload']).encode()).hexdigest(),
                  node_hash=node_hash(nodes),node_count=len(nodes))
    with pytest.raises(ValueError):publisher._verified_numbered_envelope(result)


def test_real_publish_reconstructs_parent_before_any_write(monkeypatch):
    result = envelope()
    calls=[]
    parent=source_fixture()
    monkeypatch.setattr(publisher,'certify_numbered_configuration',lambda client,number:(calls.append(('parent',number)) or parent))
    import src.backend.backtest_fixed_v4_certification as certification
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda number:(calls.append(('source',number)) or 'a'*64))
    def before_layout(client):
        assert calls==[('parent',42),('source',77)]
        raise RuntimeError('stop before database layout/write')
    monkeypatch.setattr(publisher,'verify_tables',before_layout)
    with pytest.raises(publisher.PublicationStageError) as error:
        publisher.publish_configuration(object(),object(),result)
    assert error.value.safe_diagnostic.startswith('layout:')
    assert isinstance(error.value.__cause__, RuntimeError)
    assert calls==[('parent',42),('source',77)]


def test_resealed_inheritance_tamper_rejected_before_layout(monkeypatch):
    result=envelope()
    result['payload']['strategy']['parameters']['execution']['tick_size']=.02
    nodes=encode_nodes(result['payload'])
    result.update(payload_hash=sha256(canonical_json(result['payload']).encode()).hexdigest(),node_hash=node_hash(nodes),node_count=len(nodes))
    monkeypatch.setattr(publisher,'certify_numbered_configuration',lambda *args:source_fixture())
    import src.backend.backtest_fixed_v4_certification as certification
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda number:'a'*64)
    monkeypatch.setattr(publisher,'verify_tables',lambda client:pytest.fail('tampered publication reached database layout'))
    with pytest.raises(ValueError,match='certified inheritance'):
        publisher.publish_configuration(object(),object(),result)


def test_selected_revision_actual_uuid_and_plan_crosscheck(monkeypatch):
    result=envelope()
    from test_fixed_structural_lot_native import cert
    certified=cert(result['payload'])
    monkeypatch.setattr(reader,'certify_numbered_configuration',lambda client,number:certified)
    revision=certified.revision()
    assert reader.selected_numbered_revision(revision_id=revision['revision_id'],client=object())==revision
    with pytest.raises(ValueError):reader.selected_numbered_revision(revision_id='strategy-one-77:00000000-0000-0000-0000-000000000000',client=object())
    with pytest.raises(ValueError):reader.selected_numbered_revision(revision_id=revision['revision_id'],run_plan_id='foreign',client=object())


def test_actual_normalized_reader_rederives_registered_child(monkeypatch):
    result=envelope()
    attempt='00000000-0000-0000-0000-000000000077'
    class Client:
        def execute(self,query):
            assert query.startswith('SELECT')
            assert 'strategy_number=77' in query
            if 'release_attempt_id,strategy_id' in query:
                return json.dumps(dict(release_attempt_id=attempt,strategy_id=result['payload']['strategy']['strategy_id'],
                    source_candidate_id=result['source_candidate_id'],source_candidate_hash=result['source_candidate_hash'],
                    payload_hash=result['payload_hash'],node_count=result['node_count'],node_hash=result['node_hash']))
            return '\n'.join(json.dumps(row) for row in encode_nodes(result['payload']))
    original=reader.certify_numbered_configuration
    calls=[]
    def parent_transport(client,number=1):
        if number==42:
            calls.append(number)
            return source_fixture()
        return original(client,number)
    monkeypatch.setattr(reader,'certify_numbered_configuration',parent_transport)
    certified=original(Client(),77)
    assert certified.payload==result['payload']
    assert certified.attempt_id==attempt
    assert calls==[42]


def test_legacy42_payload_preserved_and74_not_selected():
    parent=source_fixture()
    before=deepcopy(parent.payload)
    assert reader.is_numbered_fixed_configuration(parent.payload)
    reader._validate_strategy_two_payload(parent.payload)
    assert selected.declared_fixed_structural_lot_contract(42) is None
    assert selected.declared_fixed_structural_lot_contract(74) is None
    own=envelope()['payload']
    for key in ('execution','sizing'):
        assert own['strategy']['parameters'][key]==before['strategy']['parameters'][key]
    for key in ('accounts','assignments'):
        assert own.get(key)==before.get(key)
    assert parent.payload==before


def test_real77_compiler_exact_envelope_under_explicit_unsealed_source_seam(monkeypatch):
    import src.backend.backtest_fixed_v4_certification as certification
    from pipelines.strategy_one.strategy_seventy_seven_configuration import compile_strategy_seventy_seven_configuration
    observed=[]
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda number:observed.append(number))
    result=compile_strategy_seventy_seven_configuration(source_fixture(),**APPROVAL)
    assert set(result)=={'source_candidate_id','source_candidate_hash','payload_hash','node_hash','node_count','payload'}
    publisher._verified_numbered_envelope(result)
    assert observed==[77]


@pytest.mark.parametrize('missing',['rule','source','adapter'])
def test_selected_marker_conjunction_cannot_be_partial(monkeypatch,missing):
    from dataclasses import replace
    contract=selected.declared_fixed_structural_lot_contract(77)
    release=contract.release
    if missing=='rule':changes={'rule_set_contracts':release.rule_set_contracts[:-1]}
    elif missing=='source':changes={'input_contracts':release.input_contracts[:-1]}
    else:changes={'input_contracts':tuple(x for x in release.input_contracts if x!='declared-numbered-fixed-policy-adapter@1')}
    altered=replace(release,**changes,approved_digest='')
    altered=replace(altered,approved_digest=altered.digest())
    monkeypatch.setattr(selected,'numbered_strategy',lambda number:altered)
    with pytest.raises(ValueError,match='semantic companions'):
        selected.declared_fixed_structural_lot_contract(77)


def test_actual_publish_collision_after_reconstruction_has_no_inserts(monkeypatch):
    result=envelope()
    calls=[]
    monkeypatch.setattr(publisher,'certify_numbered_configuration',lambda client,number:(calls.append(('parent',number)) or source_fixture()))
    import src.backend.backtest_fixed_v4_certification as certification
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda number:(calls.append(('source',number)) or 'a'*64))
    monkeypatch.setattr(publisher,'verify_tables',lambda client:None)
    class Client:
        def execute(self,query):
            calls.append(('select',query))
            assert query.startswith('SELECT')
            return json.dumps({'release_attempt_id':'00000000-0000-0000-0000-000000000077','payload_hash':'f'*64})
    class Keeper:
        def create(self,*args,**kwargs):calls.append(('lock',args[0]))
        def delete(self,*args,**kwargs):calls.append(('unlock',args[0]))
    with pytest.raises(publisher.PublicationStageError) as error:
        publisher.publish_configuration(Client(),Keeper(),result)
    assert error.value.safe_diagnostic.startswith('existing_release:')
    assert isinstance(error.value.__cause__,RuntimeError)
    assert 'different immutable release' in str(error.value.__cause__)
    assert calls[:2]==[('parent',42),('source',77)]
    assert len([x for x in calls if x[0]=='select'])==1
