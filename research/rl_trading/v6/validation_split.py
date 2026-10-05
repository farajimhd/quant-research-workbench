"""Explicit generation-only extension; legacy training/UI split stays frozen."""
import json
import random
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash

VERSION = 'rl-v6-validation-extension-split-v1'
DAYS = ('2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03')

def read_split(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('version')!=VERSION or data.get('hash')!=digest({k:v for k,v in data.items() if k!='hash'})
        or data.get('context_day')!='2026-08-25' or data.get('days')!=list(DAYS)
        or type(data.get('seed')) is not int or set(data.get('roles',{}))!=set(DAYS)):
        raise ValueError('Invalid frozen seven-session extension split')
    sealed=set(random.Random(data['seed']).sample(list(DAYS),3))
    expected={day:('sealed_test' if day in sealed else 'development') for day in DAYS}
    if data['roles']!=expected or data.get('sealed_policy')!='generation_and_integrity_audit_only; no UI, training, tuning, or performance inspection':
        raise ValueError('Random draw or sealed generation policy changed')
    return data

def dataset_split(data, runtime):
    binding=data.get('validation_split')
    if binding is None:return None
    path=Path(binding['path']).resolve()
    if not path.is_relative_to(Path(runtime).resolve()) or file_hash(path)!=binding['sha256']:
        raise ValueError('Extension split receipt changed or escaped runtime')
    split=read_split(path)
    if split['hash']!=binding['hash']:raise ValueError('Extension split identity changed')
    return split

def generation_role(day, split=None):
    from research.rl_trading.v6.split import role
    value=str(day)
    if split is None or value==split['context_day']:return role(day)
    if value not in split['roles']:raise ValueError('Day outside frozen extension')
    return split['roles'][value]
