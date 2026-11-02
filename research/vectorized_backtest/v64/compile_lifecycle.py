"""Keep Dynamo specialization state local to a compiled callable's lifetime.

Repeated closures share their Python code object by default. Dynamo attaches
guarded variants to that object, so unrelated short-lived captures can exhaust
one function's recompile budget. An identical private code object separates
those lifetimes; Inductor's disk cache remains responsible for kernel reuse.

All vectorized-backtest versions use this shared factory, so copies inherit the
same ownership boundary. A single callable still obeys the configured guard
budget; this does not promise unlimited shapes inside one retained callable.
"""
from types import FunctionType,MethodType
from weakref import finalize
import torch

_compile=torch.compile
_reset_code=torch._dynamo.reset_code


def private_callable(function):
    """Clone identity only: bytecode, globals, defaults and closure cells stay exact."""
    if isinstance(function,MethodType):
        return MethodType(private_callable(function.__func__),function.__self__)
    if not isinstance(function,FunctionType):
        raise TypeError('Scoped compilation requires a Python function or bound method')
    result=FunctionType(function.__code__.replace(),function.__globals__,function.__name__,function.__defaults__,function.__closure__)
    result.__kwdefaults__=function.__kwdefaults__
    result.__annotations__=function.__annotations__
    result.__qualname__=function.__qualname__
    result.__module__=function.__module__
    result.__doc__=function.__doc__
    result.__dict__.update(function.__dict__)
    return result


def scoped_compile(function=None,**kwargs):
    """Same compiler/options, private per-callable guards; no fallback or limit change."""
    if function is None:return lambda fn:scoped_compile(fn,**kwargs)
    private=private_callable(function)
    compiled=_compile(private,**kwargs)
    # Dynamo's auxiliary maps can retain a code object beyond the function.
    # Retire only this owner's private guards, never another live capture or
    # the shared on-disk Inductor/Triton artifact caches.
    code=private.__func__.__code__ if isinstance(private,MethodType) else private.__code__
    retirement=finalize(compiled,_reset_code,code)
    retirement.atexit=False
    return compiled
