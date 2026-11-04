"""Bounded review-only derived arrays; sealed bars remain the source authority."""
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
from .distributed_assets import sha
from .identity import digest
from .publication import write_json
from .runtime import require_runtime


@contextmanager
def _locked(root):
    import msvcrt
    with (root/'commit.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):handle.write(b'1');handle.flush()
        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def materialize_cached(root, session, identity, tickers, clocks, reference, *,
                       maximum_bytes=160*2**30, reserve_bytes=200*2**30):
    from .shard_features import materialize, SPECS
    from research.market_shards.v1 import data, contract, source
    root=Path(root);require_runtime(root);root.mkdir(parents=True,exist_ok=True)
    if identity.get('calibration_ticks') is not None:
        raise ValueError('Review cache requires full-session features')
    files=[Path(__file__),Path(__file__).with_name('shard_features.py'),
           Path(data.__file__),Path(contract.__file__),Path(source.__file__)]
    binding=dict(version=1,identity=identity,code={p.name:sha(p) for p in files},
        tickers=digest(np.asarray(tickers).tolist()),
        clocks=digest(np.asarray(clocks).tolist()),
        reference=__import__('hashlib').sha256(np.asarray(reference).tobytes()).hexdigest())
    key=digest(binding);folder=root/key;receipt=folder/'complete.json'
    if receipt.exists():
        meta=json.loads(receipt.read_bytes())
        if meta['binding']!=binding:raise ValueError('Derived feature cache binding changed')
        # Recheck original sealed inputs, including historical bars, on every hit.
        for day,name in meta['source_files']:
            seal_path=session.root/'bars'/day/'complete.json'
            if sha(seal_path)!=session.manifest['shard_seals'][day]:raise ValueError('Cached source seal changed')
            seal=json.loads(seal_path.read_bytes());path=seal_path.parent/(name+'.parquet')
            if sha(path)!=seal['files'][path.name]:raise ValueError('Cached source bars changed')
            session.verified_files.add((day,name))
        array_path=folder/'values.npy'
        if sha(array_path)!=meta['sha256']:raise ValueError('Derived feature cache bytes changed')
        result=np.load(array_path,allow_pickle=False)
        if result.shape!=(len(clocks),len(SPECS)) or result.dtype!=np.float32:
            raise ValueError('Derived feature cache layout changed')
        return result,dict(hit=True,key=key,bytes=meta['bytes'])
    expected=len(clocks)*len(SPECS)*4+4096
    # No deletion/eviction or alternate disk. Reserve before expensive preparation.
    if shutil.disk_usage(root).free<reserve_bytes+expected:
        raise MemoryError('Review feature cache free-space reserve unavailable')
    result=materialize(session,tickers,clocks,reference)
    with _locked(root):
        if receipt.exists():
            # Another identical request committed while this one prepared.
            meta=json.loads(receipt.read_bytes())
            path=folder/'values.npy'
            if meta['binding']!=binding or sha(path)!=meta['sha256']:
                raise ValueError('Concurrent derived feature cache changed')
            existing=np.load(path,allow_pickle=False)
            if not np.array_equal(existing,result,equal_nan=True):
                raise ValueError('Concurrent feature preparation differs')
            return result,dict(hit=False,key=key,bytes=meta['bytes'])
        used=sum(p.stat().st_size for p in root.glob('*/values.npy'))
        if used+expected>maximum_bytes or shutil.disk_usage(root).free<reserve_bytes+expected:
            raise MemoryError('Review feature cache storage budget exhausted')
        folder.mkdir(exist_ok=True)
        temporary=folder/(uuid4().hex+'.npy')
        try:
            with temporary.open('wb') as handle:
                np.save(handle,result,allow_pickle=False);handle.flush();os.fsync(handle.fileno())
            temporary.replace(folder/'values.npy')
            meta=dict(binding=binding,source_files=sorted(session.verified_files),
                      sha256=sha(folder/'values.npy'),bytes=(folder/'values.npy').stat().st_size)
            write_json(receipt,meta)
        finally:temporary.unlink(missing_ok=True)
    return result,dict(hit=False,key=key,bytes=meta['bytes'])
