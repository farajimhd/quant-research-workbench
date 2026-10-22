import ast,gc,weakref
from pathlib import Path
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.compile_lifecycle import private_callable,scoped_compile


def closure(width):
    ids=torch.arange(width,dtype=torch.float64)
    def transition(x):
        x.add_(ids.sum())
        return x*2
    return transition


def test_identity_clone_preserves_every_execution_input():
    f=closure(616);g=private_callable(f)
    assert f.__code__ is not g.__code__ and f.__code__.co_code==g.__code__.co_code
    assert f.__closure__ is g.__closure__ and f.__globals__ is g.__globals__ and f.__defaults__ is g.__defaults__
    assert torch.equal(f(torch.ones(3)),g(torch.ones(3)))
    class C:
        def method(self,x):return x+1
    c=C();assert torch.equal(private_callable(c.method)(torch.ones(3)),c.method(torch.ones(3)))


def test_reproduce_original_limit_then_survive_160_scoped_shapes():
    from torch._dynamo.exc import FailOnRecompileLimitHit
    torch._dynamo.reset()
    with torch._dynamo.config.patch(recompile_limit=4,accumulated_recompile_limit=8):
        with pytest.raises(FailOnRecompileLimitHit):
            for i in range(12):torch.compile(closure(i+1),backend='eager',fullgraph=True,dynamic=False)(torch.ones(3))
    torch._dynamo.reset()
    with torch._dynamo.config.patch(recompile_limit=4,accumulated_recompile_limit=8):
        for i in range(160):
            function=closure(i+1);expected=function(torch.ones(3));actual=scoped_compile(function,backend='eager',fullgraph=True,dynamic=False)(torch.ones(3))
            assert torch.equal(actual,expected)
    torch._dynamo.reset()


def test_private_functions_are_released_with_the_owner():
    f=private_callable(closure(802));reference=weakref.ref(f);code=weakref.ref(f.__code__)
    compiled=scoped_compile(f,backend='eager',fullgraph=True,dynamic=False);compiled(torch.ones(3))
    actual=compiled._torchdynamo_orig_callable;actual_code=weakref.ref(actual.__code__)
    del compiled,actual,f;gc.collect()
    assert reference() is None and code() is None and actual_code() is None


def test_retiring_one_owner_preserves_another_live_compilation():
    compilations=[]
    def backend(graph,args):compilations.append(graph);return graph.forward
    one=scoped_compile(closure(616),backend=backend,fullgraph=True,dynamic=False)
    two=scoped_compile(closure(802),backend=backend,fullgraph=True,dynamic=False)
    one(torch.ones(3));expected=two(torch.ones(3));count=len(compilations)
    del one;gc.collect()
    assert torch.equal(two(torch.ones(3)),expected) and len(compilations)==count


def test_all_version_compile_calls_use_shared_factory():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for path in root.rglob('*.py'):
        if path.name.startswith('test') or path.name=='compile_lifecycle.py':continue
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        direct=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='torch' and n.func.attr=='compile']
        assert not direct,str(path)
