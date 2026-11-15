"""Narrow, auditable execution-only checkpoint/reporting transition for V2."""
import copy
import re
from .config import Config
from ..v5.shards.contract import digest
from ..v5.checkpoint_transition_v1 import state_hash
from ..v5.wandb_identity_v1 import signature,EXPERIMENT_KEYS


def validate_contract(old,new):
    if digest(old['design'])!=digest(Config().manifest()) or digest(new['design'])!=digest(old['design']):
        raise ValueError('Approved V2 architecture/objective must remain exact')
    left,right=copy.deepcopy(old),copy.deepcopy(new)
    for contract in (left,right):
        if not re.fullmatch('[0-9a-f]{64}',contract.pop('implementation','')):
            raise ValueError('Explicit source implementation SHA required')
        backend=contract['optimization'].pop('student_attention_backend','default')
        if backend not in ('default','cudnn'):raise ValueError('Unqualified attention backend')
    if digest(left)!=digest(right):
        raise ValueError('Only source implementation and student backend may change')


def prepare_state(parent,new_contract):
    """No model/optimizer/scheduler/RNG/cursor/history edits, no parent mutation."""
    validate_contract(parent['contract'],new_contract)
    original=state_hash(parent)
    prepared=copy.deepcopy(parent);prepared['contract']=copy.deepcopy(new_contract)
    restored=copy.deepcopy(prepared);restored['contract']=parent['contract']
    if state_hash(restored)!=original or state_hash(parent)!=original:
        raise ValueError('Execution transition altered training state')
    return prepared


def prepare_identity(identity,old_manifest,new_manifest):
    """Return a same-run identity ONLY for the validated implementation bridge."""
    if identity['signature']!=signature(old_manifest):raise ValueError('Original W&B signature differs')
    validate_contract(old_manifest['model'],new_manifest['model'])
    for key in EXPERIMENT_KEYS:
        if key!='model' and old_manifest.get(key)!=new_manifest.get(key):
            raise ValueError('Reporting scientific setting changed: '+key)
    result=copy.deepcopy(identity);result['signature']=signature(new_manifest)
    return result
