"""Reuse certified streaming and canonical GPU time/price preparation unchanged."""
import json
from pathlib import Path
import numpy as np
import torch
from ..phase1_v7.full_data import Batch,prepare_raw,gpu_inputs,loader
from ..phase1_v8.data import cache,calibrate
from ..v5.shards.phase1_contract import digest,file_hash,write_json


def calibration(dataset,split,output):
    manifest=json.loads((dataset/'manifest.json').read_text())
    if digest({k:v for k,v in manifest.items() if k!='hash'})!=manifest['hash']:raise ValueError('Calibration dataset seal changed')
    authority=json.loads(split.read_text())
    if manifest['source_split_hash']!=authority['manifest_hash']:raise ValueError('Calibration uses a different split')
    for name in ('train.npy','train-identities.json'):
        if file_hash(dataset/name)!=manifest['files'][name]:raise ValueError('Calibration training payload changed')
    identities=json.loads((dataset/'train-identities.json').read_text())
    if not all(r['end_us']<authority['cutoff_us'] for r in identities):raise ValueError('Nontraining calibration record')
    rows=np.load(dataset/'train.npy',allow_pickle=False)
    data=cache(rows);parameters,counts=calibrate(data,'joint')
    record=dict(parameters={k:v.tolist() for k,v in parameters.items()},counts=counts,
        source_split_hash=authority['manifest_hash'],training_dataset_hash=manifest['hash'],
        training_payload_sha256=file_hash(dataset/'train.npy'),windows=len(rows),
        policy='Fixed parameters fitted exclusively on the existing frozen Phase1 training subset. Full training traverses every certified training window; no validation calibration.')
    record['hash']=digest(record);write_json(output,record);return record


def read_calibration(path):
    d=json.loads(path.read_text())
    if digest({k:v for k,v in d.items() if k!='hash'})!=d['hash']:raise ValueError('Calibration seal changed')
    return d
