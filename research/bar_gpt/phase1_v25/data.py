"""Verified existing shards and frozen training-only V24 transform buffers."""
import json,time
from pathlib import Path
import numpy as np
import torch
from ..phase0_v3.experiment import cache
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import file_hash,digest


def load(root):
    started=time.time();dataset=root/'phase1-v8-learned-mae-20261117/dataset';manifest=verify(dataset)
    supplement=root/'phase1-v19-training-pool-20261121';sm=json.loads((supplement/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or sm['split_hash']!=manifest['source_split_hash'] or file_hash(supplement/'train.npy')!=sm['files']['train.npy']:raise ValueError('Supplement provenance')
    cp=root/'phase1-v24-xl-20261122/e05-w384-d6-s559/checkpoint_last.pt';receipt=json.loads((cp.parent/'result.json').read_text())
    if file_hash(cp)!=receipt['checkpoint_sha']:raise ValueError('Calibration source identity')
    saved=torch.load(cp,map_location='cpu',weights_only=False);s=saved['model']
    calibration={k:s[v].tolist() for k,v in dict(mean='mean',scale='scale',input_mean='bar.mean',input_scale='bar.scale',reference='bar.input_reference',activity_mean='bar.activity_input_mean',activity_scale='bar.activity_input_scale').items()}
    del saved,s
    train=tuple(torch.cat((a,b)) for a,b in zip(cache(np.load(dataset/'train.npy',mmap_mode='r')),cache(np.load(supplement/'train.npy',mmap_mode='r'))))
    dev=cache(np.load(dataset/'development.npy',mmap_mode='r'))
    return train,dev,calibration,dict(data_hash=manifest['hash'],split_hash=manifest['source_split_hash'],supplement_hash=sm['hash'],calibration_source_sha=receipt['checkpoint_sha'],calibration_hash=digest(calibration),train_windows=len(train[0]),development_windows=len(dev[0]),cache_seconds=time.time()-started,sampling='uniform with replacement; no sampling weights; fixed original training-only calibration')
