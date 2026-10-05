import json
import random
import pytest
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.dataset_admission import inventory,VERSION
from research.rl_trading.v6.validation_split import VERSION as SPLIT_VERSION,DAYS
from research.rl_trading.v6.split import TRAIN,DEVELOPMENT

def test_admission_excludes_sealed_and_rejects_role_changes(tmp_path):
    seed=1;sealed=set(random.Random(seed).sample(list(DAYS),3))
    split=dict(version=SPLIT_VERSION,seed=seed,context_day='2026-08-25',days=list(DAYS),
        roles={d:'sealed_test' if d in sealed else 'development' for d in DAYS},
        sealed_policy='generation_and_integrity_audit_only; no UI, training, tuning, or performance inspection')
    split['hash']=digest(split);path=tmp_path/'split.json';path.write_text(json.dumps(split))
    expected=[dict(day=str(d),role='train') for d in TRAIN]+[dict(day=str(d),role='development') for d in DEVELOPMENT]
    expected += [dict(day=d,role='development') for d in DAYS if d not in sealed]
    admission=dict(version=VERSION,split=dict(path=str(path),sha256=file_hash(path)),
        admitted_days=[e['day'] for e in expected],sealed_days=[d for d in DAYS if d in sealed])
    admission['hash']=digest(admission);receipt=tmp_path/'admission.json';receipt.write_text(json.dumps(admission))
    data=dict(context={'day':'2026-07-30'},days=expected,development_admission=dict(path=str(receipt),sha256=file_hash(receipt)))
    assert len(inventory(data,tmp_path))==22
    data['days'].append(dict(day=next(iter(sealed)),role='development'))
    with pytest.raises(ValueError,match='unauthorized'):inventory(data,tmp_path)
    data['days'].pop();data['days'][-1]['role']='train'
    with pytest.raises(ValueError,match='unauthorized'):inventory(data,tmp_path)
