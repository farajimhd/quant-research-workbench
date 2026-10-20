"""Runtime-only compiler cache configuration and verified driver-binary reuse."""
import os,json,hashlib,shutil,sys,platform,uuid
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches as base_configure

def shared_compiler_cache(source_sha256):
    """Short, source/platform-bound compiler artifacts, never evaluation results."""
    import torch
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    if len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256):
        raise ValueError('Compiler cache requires a source SHA256')
    identity=dict(source_sha256=source_sha256,python=sys.version,torch=str(torch.__version__),
                  cuda=torch.version.cuda,system=platform.system(),machine=platform.machine())
    raw=json.dumps(identity,sort_keys=True,separators=(',',':')).encode()
    root=require_runtime(Path('D:/TradingML/runtimes/v32-cc')/hashlib.sha256(raw).hexdigest()[:20])
    root.mkdir(parents=True,exist_ok=True)
    seal=root/'identity.json'
    temporary=root/(uuid.uuid4().hex+'.tmp')
    temporary.write_bytes(raw)
    try:
        try:os.link(temporary,seal)
        except FileExistsError:pass
    finally:temporary.unlink()
    if seal.read_bytes()!=raw:raise ValueError('Compiler cache identity mismatch')
    return root

def configure_caches(runtime,**kwargs):
    base_configure(runtime,**kwargs)
    source=os.environ.get('V32_DRIVER_SOURCE')
    if not source:return
    raw=Path(source).read_bytes();expected=os.environ.get('V32_DRIVER_SOURCE_SHA256')
    if not expected or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Pinned driver seed metadata changed')
    seed=json.loads(raw);cache=Path(os.environ['V32_DRIVER_CACHE']);target=Path(os.environ['TRITON_CACHE_DIR'])/seed['key'];target.mkdir(parents=True,exist_ok=True)
    for name,h in seed['files'].items():
        if Path(name).name!=name or not name.startswith('cuda_utils.cp'+str(sys.version_info.major)+str(sys.version_info.minor)) or not name.endswith('.pyd'):raise ValueError('Driver seed must match interpreter ABI; binaries only')
        original=cache/seed['key']/name
        if hashlib.sha256(original.read_bytes()).hexdigest()!=h:raise ValueError('Qualified driver binary changed')
        if not (target/name).exists():shutil.copy2(original,target/name)
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=h:raise ValueError('Target driver binary changed')
