"""Runtime-only compiler cache configuration and verified driver-binary reuse."""
import os,json,hashlib,shutil,sys
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches as base_configure

def configure_caches(runtime,**kwargs):
    base_configure(runtime,**kwargs)
    source=os.environ.get('V30_DRIVER_SOURCE')
    if not source:return
    raw=Path(source).read_bytes();expected=os.environ.get('V30_DRIVER_SOURCE_SHA256')
    if not expected or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Pinned driver seed metadata changed')
    seed=json.loads(raw);cache=Path(os.environ['V30_DRIVER_CACHE']);target=Path(os.environ['TRITON_CACHE_DIR'])/seed['key'];target.mkdir(parents=True,exist_ok=True)
    for name,h in seed['files'].items():
        if Path(name).name!=name or not name.startswith('cuda_utils.cp'+str(sys.version_info.major)+str(sys.version_info.minor)) or not name.endswith('.pyd'):raise ValueError('Driver seed must match interpreter ABI; binaries only')
        original=cache/seed['key']/name
        if hashlib.sha256(original.read_bytes()).hexdigest()!=h:raise ValueError('Qualified driver binary changed')
        if not (target/name).exists():shutil.copy2(original,target/name)
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=h:raise ValueError('Target driver binary changed')
