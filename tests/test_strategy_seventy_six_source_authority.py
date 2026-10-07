"""Actual fresh-source mutations; no public source substitution or approval seam."""
import ast
import inspect
from pathlib import Path

import pytest

from src.backend import backtest_strategy_seventy_six_certification as source

ROOT = Path(source.__file__).parents[2]
SELF = 'src/backend/backtest_strategy_seventy_six_certification.py'


@pytest.mark.parametrize('relative', source.REQUIRED_SOURCE_FILES)
def test_each_selected_leaf_rejects_actual_source_mutation(monkeypatch, relative):
    original = Path.read_text
    target = ROOT / relative
    def read(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == target:
            if relative == SELF:
                return value.replace("    observed = []", "    observed = [None]", 1)
            return value + '\n_source_mutation_control = True\n'
        return value
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='Strategy76 pinned source changed: '+relative):
        source.certify_strategy_seventy_six_source()


def test_native_source_api_accepts_no_caller_overrides():
    assert tuple(inspect.signature(source.certify_strategy_seventy_six_source).parameters) == ()
    with pytest.raises(TypeError):
        source.certify_strategy_seventy_six_source(source_overrides={})


@pytest.mark.parametrize('change', ['missing', 'reorder', 'digest_bool', 'self_kind', 'anchor'])
def test_loaded_metadata_mutation_rejected(monkeypatch, change):
    pins = dict(source.STRATEGY76_SOURCE_AST)
    if change == 'missing':
        pins.pop(next(iter(pins)))
    elif change == 'reorder':
        first = next(iter(pins)); value = pins.pop(first); pins[first] = value
    elif change == 'digest_bool':
        pins[next(iter(pins))] = True
    elif change == 'self_kind':
        pins[SELF] = {'wrong_symbol': 'a'*64}
    else:
        pins[next(iter(pins))] = 'a'*64
    monkeypatch.setattr(source, 'STRATEGY76_SOURCE_AST', pins)
    with pytest.raises(ValueError):
        source.certify_strategy_seventy_six_source()


@pytest.mark.parametrize('change', ['duplicate_key', 'missing_key', 'import', 'signature', 'syntax'])
def test_fresh_self_metadata_envelope_rejected(monkeypatch, change):
    original = Path.read_text
    target = ROOT / SELF
    def read(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path != target:
            return value
        if change == 'duplicate_key':
            tree=ast.parse(value); node=next(n for n in tree.body if isinstance(n,ast.Assign) and n.targets[0].id=='STRATEGY76_SOURCE_AST')
            key=node.value.keys[0].value; digest=ast.literal_eval(node.value.values[0])
            return value.replace('STRATEGY76_SOURCE_AST = {', 'STRATEGY76_SOURCE_AST = {'+repr(key)+':'+repr(digest)+',',1)
        if change == 'missing_key':
            return value.replace("'pipelines/strategy_one/configuration_publisher.py':", "'foreign.py':", 1)
        if change == 'import':
            return value.replace('import ast\n','import ast\nimport os\n',1)
        if change == 'signature':
            return value.replace('def certify_strategy_seventy_six_source():','def certify_strategy_seventy_six_source(*, source_overrides=None):')
        return value + '\ninvalid source : :\n'
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(ValueError):
        source.certify_strategy_seventy_six_source()


def test_excluded_backend_dependency_changes_actual_fingerprint(monkeypatch):
    from src.backend import historical_runtime_versions as versions
    candidates = sorted(set(p.relative_to(ROOT).as_posix()
        for directory in ('src/backend','src/trading_runtime')
        for p in (ROOT/directory).rglob('*.py')) - set(source.REQUIRED_SOURCE_FILES))
    assert candidates
    target=ROOT/candidates[0]
    before=versions.backend_source_fingerprint()
    original=Path.read_text
    def read(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\n_fingerprint_mutation_control=True\n' if path==target else text
    monkeypatch.setattr(Path,'read_text',read)
    assert versions.backend_source_fingerprint() != before


@pytest.mark.parametrize('git_response,expected', [('wrong_head','checkout HEAD'),('dirty','Commit the reviewed')])
def test_publisher_rejects_unapproved_commit_or_dirty_excluded_dependency(monkeypatch, git_response, expected):
    from scripts.clickhouse import publish_strategy_seventy_six_configuration as cli
    calls=[]
    def git(argv,**kwargs):
        calls.append(argv)
        if argv[1]=='rev-parse':
            return ('0'*40 if git_response=='wrong_head' else 'a'*40)+'\n'
        return ' M src/backend/excluded_fixture.py\n'
    monkeypatch.setattr(cli.subprocess,'check_output',git)
    with pytest.raises(ValueError,match=expected):
        cli.approved_source('a'*40)
    assert len(calls)==(1 if git_response=='wrong_head' else 2)


@pytest.mark.parametrize('dirty', [' M research/mlops/clickhouse.py\n',' M notebooks/unrelated.ipynb\n','?? unrelated-untracked.txt\n'])
def test_whole_checkout_dirty_gate_rejects_off_path_before_source_or_credentials(monkeypatch, dirty):
    from scripts.clickhouse import publish_strategy_seventy_six_configuration as cli
    calls=[]
    def git(argv,**kwargs):
        calls.append(argv)
        if argv==['git','rev-parse','HEAD']:
            return 'a'*40+'\n'
        assert argv==['git','status','--porcelain','--untracked-files=all']
        return dirty
    def forbidden(*args,**kwargs):
        pytest.fail('Dirty checkout must fail before source proof, credentials or publisher')
    monkeypatch.setattr(cli.subprocess,'check_output',git)
    monkeypatch.setattr(cli,'certify_numbered_fixed_v4_projection',forbidden)
    monkeypatch.setattr(cli,'backend_source_fingerprint',forbidden)
    monkeypatch.setattr(cli,'publish_configuration',forbidden)
    with pytest.raises(ValueError,match='Commit the reviewed implementation'):
        cli.approved_source('a'*40)
    assert len(calls)==2


def test_whole_checkout_clean_gate_preserves_source_proof_then_fingerprint(monkeypatch):
    from scripts.clickhouse import publish_strategy_seventy_six_configuration as cli
    calls=[]
    def git(argv,**kwargs):
        calls.append(tuple(argv))
        assert argv in (['git','rev-parse','HEAD'],['git','status','--porcelain','--untracked-files=all'])
        return 'a'*40+'\n' if argv[1]=='rev-parse' else ''
    monkeypatch.setattr(cli.subprocess,'check_output',git)
    monkeypatch.setattr(cli,'certify_numbered_fixed_v4_projection',lambda number:calls.append(('source',number)))
    def fingerprint():
        calls.append(('fingerprint',));return 'b'*64
    monkeypatch.setattr(cli,'backend_source_fingerprint',fingerprint)
    assert cli.approved_source('a'*40)=='b'*64
    assert calls[-2:]==[('source',76),('fingerprint',)]
