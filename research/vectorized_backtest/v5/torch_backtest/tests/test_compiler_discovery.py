"""Installing the pinned overlay invalidates earlier negative discovery probes."""
import importlib.util,sys,os
from torch.utils import _triton
from research.vectorized_backtest.v5.torch_backtest import runtime


def test_configured_overlay_refreshes_cached_compiler_availability(tmp_path,monkeypatch):
    target=tmp_path/'vectorized_backtest/torch_backtest_v3/dependencies/triton-3.7.1.post27/triton/runtime/tcc'
    target.mkdir(parents=True);(target/'tcc.exe').write_bytes(b'fixture')
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    original=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name,*a,**k:None if name=='triton' else original(name,*a,**k))
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setenv('PYTHONPATH',os.environ.get('PYTHONPATH',''))
    monkeypatch.setenv('CC','placeholder');monkeypatch.delenv('CC')
    cleared=[]
    class Probe:
        def cache_clear(self):cleared.append(True)
    monkeypatch.setattr(_triton,'has_triton_package',Probe())
    runtime.configure_compiler()
    assert cleared and str(target/'tcc.exe')==os.environ['CC']
