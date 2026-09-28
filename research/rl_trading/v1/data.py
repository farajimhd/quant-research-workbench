"""Verified on-disk session shards with GPU-resident window assembly."""
from __future__ import annotations

from pathlib import Path
from datetime import date
import math
import os
import warnings

import numpy as np
import torch

from research.rl_trading.v1.common import file_hash, bounds, digest
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from src.market_engine.level_book_store import read, write

ARRAYS = ('features','volume_60s','execution','closeable','time_us','slots','rank','held_slots',
    'actions','action_mask','lots','lot_slots','account','reward','return_to_go','done')


class SessionShard:
    def __init__(self, root: Path, *, verify: bool = True):
        self.root = Path(root).resolve()
        self.plan = read(self.root/'plan.json')
        self.complete = read(self.root/'complete.json')
        if (self.plan['plan_hash'] != digest({k:v for k,v in self.plan.items() if k != 'plan_hash'})
                or self.complete['plan_hash'] != self.plan['plan_hash'] or
                not math.isfinite(float(self.complete['teacher_profit'])) or
                self.complete['teacher_optimality'] != 'approximate_beam' and
                self.complete['teacher_optimality'] != 'proven_within_grid'):
            raise ValueError('Training shard completion mismatch')
        if tuple(self.plan['feature_names']) != FEATURE_NAMES:
            raise ValueError('Training feature contract changed')
        base_root = self.plan.get('base_shard_root')
        if base_root:
            base = SessionShard(Path(base_root),verify=verify)
            overlay = self.plan.get('overlay_kind','current_account')
            required = ({'account.npy'} if overlay == 'current_account' else
                {name+'.npy' for name in ('slots','rank','held_slots','actions',
                    'action_mask','lots','lot_slots','account','reward','return_to_go','done')}
                if overlay == 'cost_labels' else set())
            if (base.plan['plan_hash'] != self.plan['base_plan_hash'] or
                    file_hash(base.root/'complete.json') != self.plan['base_complete_hash'] or
                    not required or set(self.complete['files']) != required or
                    base.plan['date'] != self.plan['date'] or
                    base.plan['tickers'] != self.plan['tickers']):
                raise ValueError('Account overlay source certificate changed')
            if overlay == 'cost_labels':
                parity_path = self.root/'teacher_replay_parity.json'
                if (not parity_path.is_file() or
                        file_hash(parity_path) != self.complete.get('teacher_replay_parity_hash')):
                    raise ValueError('Cost label replay certificate changed')
                parity = read(parity_path)
                if (parity.get('mask_and_state_parity') is not True or
                        not math.isclose(float(parity['profit']),
                            float(self.complete['teacher_profit']),abs_tol=.1)):
                    raise ValueError('Cost label replay parity failed')
            self.arrays = dict(base.arrays)
            names = tuple(name[:-4] for name in self.complete['files'])
        else:
            if set(self.complete['files']) != {name+'.npy' for name in ARRAYS}:
                raise ValueError('Training shard file certificate is incomplete')
            self.arrays = {}
            names = ARRAYS
        if verify:
            for name,expected in self.complete['files'].items():
                if file_hash(self.root/name) != expected:
                    raise ValueError('Training shard file hash changed: '+name)
        for name in names:
            self.arrays[name] = np.load(self.root/(name+'.npy'),mmap_mode='r',allow_pickle=False)
        self.feature_cache = None
        rows = self.complete['rows']
        tickers = self.plan['tickers']
        if (self.arrays['features'].shape != (len(tickers),SECONDS,len(FEATURE_NAMES))
                or self.arrays['execution'].shape != (len(tickers),SECONDS,3)
                or self.arrays['closeable'].shape != (len(tickers),SECONDS)
                or self.arrays['slots'].shape != (rows,self.plan['top_n'])
                or self.arrays['actions'].shape != (rows,self.plan['max_orders'])
                or self.arrays['action_mask'].shape != (rows,self.plan['max_orders'],
                    1+self.plan['top_n']+self.plan['max_lots'])
                or self.arrays['account'].shape != (rows,3)
                or self.arrays['time_us'].shape != (rows,)
                or np.any(np.diff(self.arrays['time_us']) != 1_000_000)
                or not self.arrays['done'][-1] or np.any(self.arrays['done'][:-1])):
            raise ValueError('Training shard tensor shape or terminal contract changed')

    def release_mapped_pages(self):
        """Drop pages touched by a streamed session while retaining its file contract.

        Windows keeps pages of live NumPy memory maps in the process working set.
        Across many sessions this can exhaust host RAM even though only one
        session is resident on the GPU. Reopening the certified files leaves
        lightweight maps ready for the next epoch and unmaps the touched pages.
        Call only after consumers of the old arrays have finished.
        """
        previous = self.arrays
        remapped = {name: np.load(Path(array.filename),mmap_mode='r',allow_pickle=False)
                    for name,array in previous.items()}
        self.arrays = remapped
        for array in previous.values():
            array._mmap.close()
        if self.feature_cache is not None:
            cached = self.feature_cache
            self.feature_cache = np.load(Path(cached.filename),mmap_mode='r',allow_pickle=False)
            cached._mmap.close()

    def attach_feature_cache(self, cache_root: Path):
        location = Path(cache_root)/self.plan['plan_hash']
        certificate = read(location/'complete.json')
        expected = dict(version='rl-trading-feature-cache-v1',
            plan_hash=self.plan['plan_hash'],
            source_feature_hash=self._source_feature_hash(),
            shape=list(self.arrays['features'].shape),dtype='float16')
        if any(certificate.get(key) != value for key,value in expected.items()):
            raise ValueError('Feature cache source contract changed')
        path = location/'features.npy'
        if file_hash(path) != certificate.get('cache_hash'):
            raise ValueError('Feature cache hash changed')
        cached = np.load(path,mmap_mode='r',allow_pickle=False)
        if cached.shape != self.arrays['features'].shape or cached.dtype != np.float16:
            raise ValueError('Feature cache tensor contract changed')
        if self.feature_cache is not None:
            self.feature_cache._mmap.close()
        self.feature_cache = cached
        return certificate

    def _source_feature_hash(self):
        plan,complete = self.plan,self.complete
        while 'features.npy' not in complete['files']:
            base_root = plan.get('base_shard_root')
            if not base_root:
                raise ValueError('Feature cache has no certified source tensor')
            plan = read(Path(base_root)/'plan.json')
            complete = read(Path(base_root)/'complete.json')
        return complete['files']['features.npy']

    def build_feature_cache(self, cache_root: Path, *, chunk_tickers: int = 128):
        if chunk_tickers < 1:
            raise ValueError('Feature cache chunk must be positive')
        location = Path(cache_root)/self.plan['plan_hash']
        if (location/'complete.json').is_file():
            return self.attach_feature_cache(cache_root)
        location.mkdir(parents=True,exist_ok=True)
        target = location/'features.npy'
        temporary = location/f'features.{os.getpid()}.npy.tmp'
        source = self.arrays['features']
        output = np.lib.format.open_memmap(temporary,mode='w+',dtype=np.float16,
            shape=source.shape)
        try:
            for start in range(0,source.shape[0],chunk_tickers):
                chunk = np.asarray(source[start:start+chunk_tickers]).astype(np.float16)
                if not np.isfinite(chunk).all():
                    raise ValueError('Feature cache cannot represent source in float16')
                output[start:start+len(chunk)] = chunk
            output.flush()
        finally:
            del output
        os.replace(temporary,target)
        certificate = dict(version='rl-trading-feature-cache-v1',
            plan_hash=self.plan['plan_hash'],source_feature_hash=self._source_feature_hash(),
            shape=list(source.shape),dtype='float16',cache_hash=file_hash(target))
        write(location/'complete.json',certificate)
        self.attach_feature_cache(cache_root)
        return certificate

    def to_gpu(self, device: torch.device, ticker_vocab: dict[str,int],
               *, reserve_fraction: float = .3):
        if device.type != 'cuda':
            raise ValueError('Training data must be assembled on a CUDA GPU')
        if not 0 < reserve_fraction < 1:
            raise ValueError('GPU reserve fraction must be between zero and one')
        needed = sum(value.nbytes//2 if key == 'features' else value.nbytes
                     for key,value in self.arrays.items() if key not in
                     ('volume_60s','execution','closeable'))
        # The preceding session's tensors may have been freed into PyTorch's
        # cache. Driver free memory alone then understates usable capacity.
        torch.cuda.empty_cache()
        available,_ = torch.cuda.mem_get_info(device)
        if needed > available*(1-reserve_fraction):
            raise MemoryError(f'Session shard needs {needed/2**30:.1f} GiB before training activations; split the shard')
        return GpuSession(self,device,ticker_vocab)


class GpuSession:
    def __init__(self, source: SessionShard, device: torch.device, ticker_vocab: dict[str,int]):
        self.plan = source.plan
        self.device = device
        self.values = {}
        for name,value in source.arrays.items():
            if name in ('volume_60s','execution','closeable'):
                continue
            if name == 'features' and source.feature_cache is not None:
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore',message='The given NumPy array is not writable')
                    self.values[name] = torch.from_numpy(source.feature_cache).to(device)
                continue
            host = np.asarray(value).astype(np.float16,copy=True) if name == 'features' else np.asarray(value).copy()
            if name == 'features' and not np.isfinite(host).all():
                raise ValueError('Feature bank cannot be represented in float16')
            self.values[name] = torch.from_numpy(host).to(device,non_blocking=True)
        self.ticker_ids = torch.tensor([ticker_vocab.get(ticker,len(ticker_vocab)+1)
            for ticker in source.plan['tickers']],
            device=device,dtype=torch.long)
        self.rows = source.complete['rows']
        self.left_us = bounds(date.fromisoformat(source.plan['date']))[0]
        self.offsets = torch.arange(int(source.plan['history_seconds'])-1,-1,-1,device=device)

    def batch(self, indices: torch.Tensor, *, slot_override: torch.Tensor | None = None) -> dict[str,torch.Tensor]:
        values = self.values
        rows = indices.to(self.device,dtype=torch.long)
        slot = values['slots'][rows].long() if slot_override is None else slot_override.long()
        valid = slot >= 0
        selected = slot.clamp_min(0)
        second = ((values['time_us'][rows]-self.left_us)//1_000_000).long()
        time = second[:,None]-self.offsets[None,:]
        history_valid = time >= 0
        time = time.clamp_min(0)
        market = values['features'][selected[:,:,None],time[:,None,:]].float()
        market = market*valid[:,:,None,None]*history_valid[:,None,:,None]
        return dict(market=market,valid=valid,ticker_id=self.ticker_ids[selected]*valid,
            rank=values['rank'][rows].float()/max(1,len(self.ticker_ids)),
            held=values['held_slots'][rows].float(),
            lots=values['lots'][rows],lot_slots=values['lot_slots'][rows].long(),
            account=values['account'][rows],actions=values['actions'][rows].long(),
            action_mask=values['action_mask'][rows],reward=values['reward'][rows],
            return_to_go=values['return_to_go'][rows],done=values['done'][rows])


def ticker_vocabulary(train_shards: list[SessionShard]) -> dict[str,int]:
    tickers = sorted({ticker for shard in train_shards for ticker in shard.plan['tickers']})
    return {ticker:index+1 for index,ticker in enumerate(tickers)}
