"""Explicit, evidence-bound resume for equivalent input-preparation engineering."""
import json
from pathlib import Path
from ..v5.shards.phase1_contract import digest,file_hash
from ..phase2_v2.train import load_checkpoint


def resume(path,contract,receipt_path=None):
    proof=json.loads(path.with_suffix('.proof.json').read_text())
    if proof['contract']==digest(contract):return load_checkpoint(path,contract),None
    if receipt_path is None:raise ValueError('Changed source requires explicit qualified engineering receipt')
    receipt=json.loads(Path(receipt_path).read_text())
    if receipt['hash']!=digest({k:v for k,v in receipt.items() if k!='hash'}):raise ValueError('Engineering receipt changed')
    previous=receipt['previous_contract']
    if receipt['new_source_revision']!=contract['source_revision']:raise ValueError('Engineering source mismatch')
    if receipt.get('transition')=='completed-pass-throughput-v1':
        allowed={'source_revision','batch_size','workers','prefetch','shard_prefetch','shard_prefetch_mib',
                 'validation_batch_size','validation_graph'}
        if {k:v for k,v in previous.items() if k not in allowed}!={k:v for k,v in contract.items() if k not in allowed}:
            raise ValueError('Throughput transition changes scientific/optimizer contract')
        if file_hash(path)!=receipt['checkpoint_sha256']:raise ValueError('Throughput parent changed')
        for item in receipt['evidence'].values():
            if file_hash(Path(item['path']))!=item['sha256']:raise ValueError('Throughput evidence changed')
        if not receipt.get('validation_parity') or not receipt.get('user_authorized_batch_change'):
            raise ValueError('Missing throughput qualification/authorization')
        state=load_checkpoint(path,previous)
        if state['phase']!='validation' or state['epoch_done']!=receipt['training_population']:
            raise ValueError('Batch/worker change requires completed training pass')
        return state,receipt
    if previous['preparation']!='gpu' or contract['preparation']!='raw_gpu':raise ValueError('Only qualified compact-to-raw GPU preparation transition permitted')
    scientific=lambda c:{k:v for k,v in c.items() if k not in ('source_revision','preparation')}
    if scientific(previous)!=scientific(contract):raise ValueError('Scientific/traversal/optimizer contract changed')
    if file_hash(path)!=receipt['checkpoint_sha256']:raise ValueError('Engineering parent checkpoint changed')
    evidence={}
    for name,item in receipt['evidence'].items():
        p=Path(item['path'])
        if file_hash(p)!=item['sha256']:raise ValueError('Qualification evidence changed')
        evidence[name]=json.loads(p.read_text())
    q=evidence['precision'];e=evidence['engineering'];s=evidence['stream']
    if not (q['bf16_accepted'] and q['preparation_prediction_parity'] and e['graph_eager_update_parity'] and
            e['checkpoint_resume_update_parity'] and e['ratio']<.2 and s['windows_per_second']>176563.):
        raise ValueError('Engineering acceptance criteria failed')
    return load_checkpoint(path,previous),receipt
