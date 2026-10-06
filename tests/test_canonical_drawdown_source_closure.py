from pathlib import Path
import pytest
from src.backend import backtest_fixed_v4_certification as cert

CORE_LEAVES = tuple(cert._DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)

@pytest.mark.parametrize('number', (1,42,*range(46,62)))
def test_all_native_routes_compose_numeric_core(monkeypatch, number):
    original = cert.certify_drawdown_measure_core_source
    calls=[]
    def checked(**kwargs):
        calls.append(True)
        return original(**kwargs)
    monkeypatch.setattr(cert,'certify_drawdown_measure_core_source',checked)
    assert len(cert.certify_numbered_fixed_v4_projection(number)) == 64
    assert calls

@pytest.mark.parametrize('number', (1,42,50,57,59))
@pytest.mark.parametrize('relative', CORE_LEAVES)
def test_real_old_route_rejects_mutated_reachable_leaf(monkeypatch,number,relative):
    original=Path.read_text
    expected=(Path(cert.__file__).parents[2]/relative).resolve()
    reads=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        if path.resolve()==expected:
            reads.append(True)
            if relative.endswith('backtest_fixed_v4_certification.py'):
                return source.replace('    core_proof = certify_drawdown_measure_core_source()', '    core_proof = "foreign-core"',1)
            return source+'\n_NUMERIC_CORE_MUTATION_PROBE = True\n'
        return source
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match=r'(Drawdown core source changed|Strategy 13 reviewed source authority changed)') as failure:
        cert.certify_numbered_fixed_v4_projection(number)
    assert reads
    if relative in ('src/trading_runtime/drawdown_measure_authority.py','src/trading_runtime/drawdown_measure_policy.py'):
        assert 'Drawdown core source changed: '+relative in str(failure.value)

def test_core_unknown_override_and_exact_keyset(monkeypatch):
    with pytest.raises(ValueError,match='override is unknown'):
        cert.certify_drawdown_measure_core_source(source_overrides={'unknown.py':Path('never-read')})
    monkeypatch.setattr(cert,'_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES',CORE_LEAVES[:-1])
    with pytest.raises(ValueError,match='keyset changed'):
        cert.certify_drawdown_measure_core_source()

@pytest.mark.parametrize('number', (1,42,50,57,59))
@pytest.mark.parametrize('mutation', ('remove-authority','remove-policy','replace-pin'))
def test_real_old_route_rejects_metadata_only_self_mutation(monkeypatch,number,mutation):
    import ast
    original=Path.read_text
    target=Path(cert.__file__).resolve()
    read_count=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        if path.resolve()!=target:
            return source
        read_count.append(True)
        tree=ast.parse(source)
        leaf='src/trading_runtime/drawdown_measure_authority.py' if mutation=='remove-authority' else 'src/trading_runtime/drawdown_measure_policy.py'
        for node in tree.body:
            if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name):
                continue
            if node.targets[0].id=='_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES' and mutation!='replace-pin':
                value=tuple(x for x in ast.literal_eval(node.value) if x!=leaf)
                node.value=ast.parse(repr(value),mode='eval').body
            if node.targets[0].id=='_DRAWDOWN_CORE_REVIEWED_AST':
                value=ast.literal_eval(node.value)
                if mutation=='replace-pin': value[leaf]='0'*64
                else: del value[leaf]
                node.value=ast.parse(repr(value),mode='eval').body
        return ast.unparse(tree)
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match='Drawdown core source metadata anchor changed'):
        cert.certify_numbered_fixed_v4_projection(number)
    assert read_count

@pytest.mark.parametrize('mutation', ('empty','wrong-count','wrong-name','reversed-self','malformed','empty-digests','many-digests','wrong-digest-type'))
def test_core_rejects_corrupted_cached_summary(monkeypatch,mutation):
    original=cert.canonical_symbol_ast_summary
    def corrupt(source,selectors):
        actual=original(source,selectors)
        if mutation=='empty': return ()
        if mutation=='wrong-count': return actual+actual
        if mutation=='wrong-name': return (('foreign-name',actual[0].digests),)
        if mutation=='reversed-self': return tuple(reversed(actual)) if len(selectors)==2 else actual
        if mutation=='malformed': return (('malformed',),)
        if mutation=='empty-digests': return ((selectors[0],()),)
        if mutation=='many-digests': return ((selectors[0],actual[0].digests*2),)
        return ((selectors[0],(None,)),)
    monkeypatch.setattr(cert,'canonical_symbol_ast_summary',corrupt)
    with pytest.raises(ValueError,match=r'Drawdown core (cached summary shape|source) changed'):
        cert.certify_drawdown_measure_core_source()


def test_cache_helper_source_is_independently_verified_before_cache_use(monkeypatch):
    original=Path.read_text
    target=(Path(cert.__file__).parents[2]/'src/backend/source_ast_summary.py').resolve()
    calls=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        return source+'\n_NUMERIC_CACHE_HELPER_MUTATION = True\n' if path.resolve()==target else source
    def corrupt(*args,**kwargs):
        calls.append(True)
        return ()
    monkeypatch.setattr(Path,'read_text',changed)
    monkeypatch.setattr(cert,'canonical_symbol_ast_summary',corrupt)
    with pytest.raises(ValueError,match=r'source_ast_summary.py:direct-ast'):
        cert.certify_drawdown_measure_core_source()
    assert not calls


@pytest.mark.parametrize('number',(1,42,50,57,59))
@pytest.mark.parametrize('mutation',('replace-value','drop-symbol','wrong-value-type'))
def test_real_old_route_rejects_reader_only_self_pin_mutation(monkeypatch,number,mutation):
    import ast
    original=Path.read_text
    target=Path(cert.__file__).resolve()
    reads=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        if path.resolve()!=target:return source
        reads.append(True)
        tree=ast.parse(source)
        for node in tree.body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='_DRAWDOWN_CORE_REVIEWED_AST':
                value=ast.literal_eval(node.value)
                pins=value['src/backend/backtest_fixed_v4_certification.py']
                symbol='certify_drawdown_measure_core_source'
                if mutation=='drop-symbol':del pins[symbol]
                else:pins[symbol]='0'*64 if mutation=='replace-value' else 123
                node.value=ast.parse(repr(value),mode='eval').body
        return ast.unparse(tree)
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match='Drawdown core source self pins changed'):
        cert.certify_numbered_fixed_v4_projection(number)
    assert reads

@pytest.mark.parametrize('relative', ('src/trading_runtime/drawdown_measure_authority.py','src/trading_runtime/drawdown_measure_policy.py','src/backend/source_ast_summary.py'))
def test_warmed_baseline_proof_fresh_reader_mutation_and_restore(monkeypatch,relative):
    control=cert.certify_numbered_fixed_v4_projection(1)
    original=Path.read_text
    target=(Path(cert.__file__).parents[2]/relative).resolve()
    reads=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        if path.resolve()==target:
            reads.append(True)
            return source+'\n_WARMED_NUMERIC_CORE_MUTATION = True\n'
        return source
    with monkeypatch.context() as scoped:
        scoped.setattr(Path,'read_text',changed)
        with pytest.raises(ValueError,match='Drawdown core source changed: '+relative):
            cert.certify_numbered_fixed_v4_projection(1)
    assert reads
    assert cert.certify_numbered_fixed_v4_projection(1)==control

@pytest.mark.parametrize('number',(1,42,50,57,59))
@pytest.mark.parametrize('mutation',('required-tuple-to-list','map-dict-to-pairs','missing-required'))
def test_real_old_route_rejects_fresh_declaration_type_or_missing_mutation(monkeypatch,number,mutation):
    import ast
    original=Path.read_text
    target=Path(cert.__file__).resolve()
    reads=[]
    def changed(path,*args,**kwargs):
        source=original(path,*args,**kwargs)
        if path.resolve()!=target:return source
        reads.append(True)
        tree=ast.parse(source)
        kept=[]
        for node in tree.body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                name=node.targets[0].id
                if name=='_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES':
                    if mutation=='missing-required':continue
                    if mutation=='required-tuple-to-list':
                        node.value=ast.parse(repr(list(ast.literal_eval(node.value))),mode='eval').body
                if name=='_DRAWDOWN_CORE_REVIEWED_AST' and mutation=='map-dict-to-pairs':
                    node.value=ast.parse(repr(list(ast.literal_eval(node.value).items())),mode='eval').body
            kept.append(node)
        tree.body=kept
        return ast.unparse(tree)
    monkeypatch.setattr(Path,'read_text',changed)
    expected={'required-tuple-to-list':'Drawdown core fresh declarations disagree','map-dict-to-pairs':'Drawdown core source self pins changed','missing-required':'Drawdown core source metadata is missing'}[mutation]
    with pytest.raises(ValueError,match=expected):
        cert.certify_numbered_fixed_v4_projection(number)
    assert reads
