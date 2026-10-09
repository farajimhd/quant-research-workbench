"""Exact, read-only reuse of certified dense structural cache observations.

Engine compatibility is established from immutable code bytes. Cached results
must then match the current opening seed, splits, full raw bars and dense quote
clock signature. Missing entries are reported misses; corrupt entries fail.
"""
import json
from hashlib import sha256
from pathlib import Path
import numpy as np
import torch
from .runtime import file_hash
from .structural import _signature,_cache_key,_load
from .prepare import tape_fingerprint


def compatible_algorithm(legacy_code,version='v4'):
    if version not in ('v4','v5'):raise ValueError('Unsupported certified dense version')
    old=Path(legacy_code);current=Path(__file__).resolve().parents[4]
    package=Path('research/vectorized_backtest')/version/'torch_backtest'
    relative=[Path('src/backend/fixed_v7_stream.py'),Path('src/backend/structural_v7_seed.py'),
              Path('src/trading_runtime/strategy_one_v7.py')]
    old_market={p.name for p in (old/'src/market_engine').glob('*.py')}
    new_market={p.name for p in (current/'src/market_engine').glob('*.py')}
    if old_market!=new_market:raise ValueError('Legacy structural engine file coverage differs')
    relative.extend(Path('src/market_engine')/name for name in sorted(new_market))
    pairs=[(package/'structural.py',Path('research/vectorized_backtest/v6/torch_backtest/structural.py'))]
    pairs.extend((p,p) for p in relative)
    hashes={};algorithm=sha256()
    for previous,present in pairs:
        a=old/previous;b=current/present
        if not a.is_file() or a.read_bytes()!=b.read_bytes():
            raise ValueError('Legacy structural engine bytes differ: '+str(previous))
        algorithm.update(previous.as_posix().encode());algorithm.update(a.read_bytes())
        hashes[str(previous)]=file_hash(a)
    offline=old/package/'offline_structure.py';prefetch=old/package/'reference_prefetch.py'
    token=sha256((algorithm.hexdigest()+file_hash(offline)+file_hash(prefetch)).encode()).hexdigest()
    hashes[str(package/'offline_structure.py')]=file_hash(offline)
    hashes[str(package/'reference_prefetch.py')]=file_hash(prefetch)
    return token,hashes


class DenseCacheReuse:
    def __init__(self,execution_root,cache_root,legacy_code,*,day,clocks,version='v4'):
        self.algorithm,self.code_hashes=compatible_algorithm(legacy_code,version)
        self.root=Path(execution_root);self.cache=Path(cache_root)
        receipt_path=self.root/'receipt.json';receipt=json.loads(receipt_path.read_text())
        if receipt['identity']['session']['day']!=day:raise ValueError('Legacy reuse session differs')
        blob=self.root/'tape.pt'
        if file_hash(blob)!=receipt['sha256']:raise ValueError('Legacy dense snapshot bytes changed')
        self.tape=torch.load(blob,map_location='cpu',weights_only=True,mmap=True)
        provenance=self.tape['provenance']
        if provenance['fingerprint']!=receipt['source_fingerprint'] or tape_fingerprint(provenance)!=receipt['source_fingerprint']:
            raise ValueError('Legacy dense snapshot provenance differs')
        self.clocks=self.tape['clocks'].numpy()
        if not np.array_equal(self.clocks,clocks):raise ValueError('Legacy dense replay clocks differ')
        tickers=self.tape['tickers']
        if len(set(tickers))!=len(tickers) or self.tape['ask'].shape!=(len(clocks),len(tickers)):
            raise ValueError('Legacy quote identity shape differs')
        self.indices={ticker:i for i,ticker in enumerate(tickers)}
        self.source_key=provenance['source_key']
        self.evidence=dict(execution_receipt_sha256=file_hash(receipt_path),tape_sha256=receipt['sha256'],
                           legacy_algorithm_sha256=self.algorithm,engine_files=self.code_hashes)

    def lookup(self,ticker,day,seed,splits,rows,asks,stamps):
        if ticker not in self.indices:return None,'listing_not_in_legacy_universe'
        indices=np.searchsorted(self.clocks,stamps)
        if np.any(indices>=len(self.clocks)) or not np.array_equal(self.clocks[indices],stamps):
            raise ValueError('Requested structural reuse clocks are not exact')
        dense_asks=np.ascontiguousarray(self.tape['ask'][:,self.indices[ticker]].numpy())
        if not np.array_equal(dense_asks[indices],asks,equal_nan=True):return None,'current_observation_quotes_differ'
        signature=_signature(ticker,day,seed,splits,rows,dense_asks,self.clocks,self.source_key,self.algorithm)
        path=self.cache/_cache_key(signature)
        if not (path/'complete.json').exists():return None,'exact_dense_signature_not_cached'
        targets,valid,receipt=_load(path,signature,len(self.clocks))
        # The unchanged streaming engine consumes the same full raw history.
        # Only the output clock-axis is reduced, with exact index selection.
        result=(targets[indices].copy(),valid[indices].copy(),dict(status='exact_dense_cache_reused',
            cache_receipt_sha256=file_hash(path/'complete.json'),cache_array_sha256=receipt['array_hash'],
            signature=signature,**self.evidence))
        return result,None
