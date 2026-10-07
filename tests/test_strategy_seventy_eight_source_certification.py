"""Real supplemental source proof; no source override/path admission API."""
from pathlib import Path
import pytest
from src.backend import backtest_strategy_seventy_eight_certification as cert


@pytest.mark.parametrize('relative,old,new', [
    ('src/trading_runtime/strategy_one_stateful.py','reentry_policy is not None or rapid or same_resistance','rapid or same_resistance'),
    ('src/backend/backtest_strategy_one_stateful.py','contract.prior_position_high_reentry_policy','None'),
    ('src/trading_runtime/prior_position_high_reentry.py','every-reentry-prior-held-high-cross@1','foreign-prior-high@1'),
    ('src/trading_runtime/strategy_initial_strong_momentum.py','first_structurally_eligible_setup','foreign_setup'),
])
def test_actual_executable_leaf_mutation_rejects(monkeypatch,relative,old,new):
    actual=Path.read_text
    target=Path(cert.__file__).parents[2]/relative
    source=actual(target,encoding='utf-8')
    if relative.endswith('strategy_initial_strong_momentum.py'):
        old="'first_setup': 'first_structurally_eligible_setup'"
        new="'first_setup': 'foreign_setup'"
    assert source.count(old)==1
    changed=source.replace(old,new);assert changed!=source
    reads=[]
    def read(path,*args,**kwargs):
        if path==target: reads.append(path);return changed
        return actual(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(ValueError,match='pinned source changed'):cert.certify_strategy_seventy_eight_source()
    assert reads==[target]


@pytest.mark.parametrize('relative', ['src/trading_runtime/strategy_one_stateful.py',
    'src/backend/backtest_strategy_one_stateful.py','src/trading_runtime/prior_position_high_reentry.py',
    'src/backend/backtest_strategy_seventy_eight_certification.py',
    'src/backend/backtest_fixed_v4_certification.py', 'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py', 'src/trading_runtime/squeeze_ladder_automatic.py'])
@pytest.mark.parametrize('tail', ['\nforeign_global = 1\n', '\nimport subprocess\n', '\ndef foreign_function():\n    return True\n'])
def test_executable_additions_and_own_envelope_reject(monkeypatch,relative,tail):
    actual=Path.read_text;target=Path(cert.__file__).parents[2]/relative
    def read(path,*args,**kwargs):return actual(path,*args,**kwargs)+(tail if path==target else '')
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(ValueError):cert.certify_strategy_seventy_eight_source()


@pytest.mark.parametrize('keyword',['source_overrides','expected_map','path','policy','strategy_number'])
def test_public_caller_cannot_supply_source_authority(keyword):
    with pytest.raises(TypeError):cert.certify_strategy_seventy_eight_source(**{keyword:{}})


@pytest.mark.parametrize('mutation',['missing','extra','unknown_dictionary','missing_symbol','extra_symbol'])
def test_loaded_metadata_keyset_and_selector_closed(monkeypatch,mutation):
    pins=dict(cert.STRATEGY78_SOURCE_AST)
    key='src/backend/backtest_strategy_seventy_eight_certification.py'
    if mutation=='missing': pins.pop(next(iter(pins)))
    elif mutation=='extra': pins['foreign.py']='a'*64
    elif mutation=='unknown_dictionary': pins['src/trading_runtime/strategy_one_stateful.py']={'propose_strategy_one_entry':'a'*64}
    else:
        pins[key]=dict(pins[key])
        if mutation=='missing_symbol':pins[key]={}
        else:pins[key]['foreign_function']='a'*64
    monkeypatch.setattr(cert,'STRATEGY78_SOURCE_AST',pins)
    with pytest.raises(ValueError):cert.certify_strategy_seventy_eight_source()


def test_own_source_positive_requires_entire_actual111():
    assert len(cert.REQUIRED_SOURCE_FILES)==111
    assert 'src/trading_runtime/strategy_one_stateful.py' in cert.REQUIRED_SOURCE_FILES
    assert 'src/backend/backtest_strategy_one_stateful.py' in cert.REQUIRED_SOURCE_FILES
    assert len(cert.certify_strategy_seventy_eight_source())==64


@pytest.mark.parametrize('relative,old,new', [
    ('src/backend/backtest_fixed_v4_certification.py','strategy_number == 78','strategy_number == 79'),
    ('src/backend/backtest_declared_ladder_plan.py','return declared_ladder_policy(SimpleNamespace(payload=configuration))','return None'),
    ('src/backend/backtest_ladder_source_authority.py',"declared = release.get('automatic_entry_policy')","declared = release.get('foreign_policy')"),
    ('src/trading_runtime/squeeze_ladder_automatic.py',"policy_id: str = 'fixed-swing-three-equal-once-session@1'","policy_id: str = 'foreign-policy@1'"),
])
def test_previous_symbol_only_surfaces_whole_body_rejects(monkeypatch,relative,old,new):
    actual=Path.read_text;target=Path(cert.__file__).parents[2]/relative
    source=actual(target,encoding='utf-8');assert source.count(old)==1
    changed=source.replace(old,new);assert changed!=source
    reads=[]
    def read(path,*args,**kwargs):
        if path==target:reads.append(path);return changed
        return actual(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(ValueError,match='pinned source changed'):cert.certify_strategy_seventy_eight_source()
    assert reads==[target]
