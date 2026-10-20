"""Runtime-only compiler cache configuration and verified driver-binary reuse."""
import os,json,hashlib,shutil,sys,platform,uuid
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches as base_configure

_HOST_ONLY={'host_store.py','preparation.py','prefetch.py','io_pipeline.py','stream_sessions.py','runtime.py'}


def compiler_source_hash(root=None):
    """All evaluator dependencies, excluding modules which only schedule I/O.

    These exclusions contain no Inductor/Triton kernel definitions. Financial,
    neural, feature, graph-construction and compiler producer bytes stay bound.
    Torch additionally validates its own generated graph/specialization keys.
    """
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    digest=hashlib.sha256()
    for version in ('v6','v7','v27','v32'):
        for path in sorted((root/version).rglob('*.py')):
            if version=='v32' and path.name in _HOST_ONLY:continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes().replace(b'\r\n',b'\n'))
    return digest.hexdigest()


def _compiler_identity(**binding):
    import torch
    return dict(**binding,python=sys.version,torch=str(torch.__version__),
                cuda=torch.version.cuda,system=platform.system(),machine=platform.machine())


def shared_compiler_cache(source_sha256):
    """Short, source/platform-bound compiler artifacts, never evaluation results."""
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    if len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256):
        raise ValueError('Compiler cache requires a source SHA256')
    identity=_compiler_identity(kernel_source_sha256=compiler_source_hash(),schema=2)
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
    bindings=root/'source-bindings';bindings.mkdir(exist_ok=True)
    binding=bindings/(source_sha256+'.json')
    if not binding.exists():binding.write_text(json.dumps(dict(source_sha256=source_sha256,**identity),sort_keys=True))
    return root


def seed_compatible_compiler_cache(previous_code,previous_source,current_source):
    """Operational one-time seed from an exited, verified legacy deployment.

    Compiler artifacts only. No data, results, populations or RNG are imported.
    Caller must audit ownership release before invoking this function.
    """
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash
    root=Path(previous_code)/'research/vectorized_backtest';digest=hashlib.sha256()
    for version in ('v6','v7','v27','v32'):
        for path in sorted((root/version).rglob('*.py')):
            digest.update(path.relative_to(root).as_posix().encode());digest.update(path.read_bytes().replace(b'\r\n',b'\n'))
    if digest.hexdigest()!=previous_source:raise ValueError('Previous compiler source binding changed')
    if compiler_source_hash(root)!=compiler_source_hash():raise ValueError('Compiler kernel dependencies differ')
    identity=_compiler_identity(source_sha256=previous_source)
    raw=json.dumps(identity,sort_keys=True,separators=(',',':')).encode()
    previous=require_runtime(Path('D:/TradingML/runtimes/v32-cc')/hashlib.sha256(raw).hexdigest()[:20])
    if (previous/'identity.json').read_bytes()!=raw:raise ValueError('Legacy compiler platform/source identity changed')
    target=shared_compiler_cache(current_source);files=[]
    for group in ('inductor','triton','extensions'):
        for path in sorted((previous/group).rglob('*')):
            if not path.is_file() or 'locks' in path.parts or path.suffix in ('.lock','.tmp'):continue
            dest=target/path.relative_to(previous);dest.parent.mkdir(parents=True,exist_ok=True)
            before=file_hash(path)
            if not dest.exists():shutil.copy2(path,dest)
            if file_hash(dest)!=before:raise ValueError('Compiler seed artifact mismatch')
            files.append(dict(path=path.relative_to(previous).as_posix(),sha256=before,bytes=path.stat().st_size))
    proof=dict(previous_source=previous_source,current_source=current_source,kernel_source_sha256=compiler_source_hash(),files=files)
    (target/('seed-'+previous_source+'.json')).write_text(json.dumps(proof,sort_keys=True))
    return dict(cache=str(target),files=len(files),bytes=sum(row['bytes'] for row in files),kernel_source_sha256=proof['kernel_source_sha256'])

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
