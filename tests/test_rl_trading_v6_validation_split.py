import json
import random
from datetime import date
from pathlib import Path
import pytest
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.validation_split import DAYS,VERSION,read_split,dataset_split,generation_role
from research.rl_trading.v6.opportunity_dataset import require_dataset


def frozen(tmp_path):
    seed=13579;sealed=set(random.Random(seed).sample(list(DAYS),3))
    data=dict(version=VERSION,context_day='2026-08-25',days=list(DAYS),seed=seed,
        roles={day:('sealed_test' if day in sealed else 'development') for day in DAYS},
        sealed_policy='generation_and_integrity_audit_only; no UI, training, tuning, or performance inspection')
    data['hash']=digest(data);path=tmp_path/'split.json';path.write_text(json.dumps(data));return path,data


def test_draw_reproducible_and_exactly_three_sealed(tmp_path):
    path,data=frozen(tmp_path)
    assert read_split(path)==data
    assert list(data['roles'].values()).count('sealed_test')==3
    assert generation_role(date(2026,8,25),data)=='development'
    for day in DAYS:assert generation_role(date.fromisoformat(day),data)==data['roles'][day]
    with pytest.raises(ValueError):generation_role(date(2026,9,4),data)
    with pytest.raises(ValueError):generation_role(date(2026,9,3))


def test_rehashed_role_swap_cannot_change_random_draw(tmp_path):
    path,data=frozen(tmp_path);day=DAYS[0]
    data['roles'][day]='development' if data['roles'][day]=='sealed_test' else 'sealed_test'
    data['hash']=digest({k:v for k,v in data.items() if k!='hash'});path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Random draw'):read_split(path)


def test_extension_denied_to_default_training_and_public_reader(tmp_path):
    path,data=frozen(tmp_path)
    binding=dict(path=str(path),sha256=file_hash(path),hash=data['hash'])
    dataset=tmp_path/'dataset.json';dataset.write_text(json.dumps(dict(validation_split=binding)))
    with pytest.raises(ValueError,match='unavailable to training'):require_dataset(dataset,runtime_root=tmp_path)
    assert dataset_split(dict(validation_split=binding),tmp_path)==data
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='receipt changed'):dataset_split(dict(validation_split=binding),tmp_path)


def test_extension_manifest_cannot_escape_runtime(tmp_path):
    path,data=frozen(tmp_path)
    with pytest.raises(ValueError,match='escaped runtime'):
        dataset_split(dict(validation_split=dict(path=str(path),sha256=file_hash(path),hash=data['hash'])),tmp_path/'other')


def test_remote_split_path_mapping_keeps_exact_hash_and_runtime_fence(tmp_path):
    path,data=frozen(tmp_path)
    remote='D:/TradingML/runtimes/extension/split.json'
    binding=dict(path=remote,sha256=file_hash(path),hash=data['hash'])
    mapper=lambda value:path if value==remote else Path(value)
    assert dataset_split(dict(validation_split=binding),tmp_path,mapper)==data
    with pytest.raises(ValueError,match='escaped runtime'):
        dataset_split(dict(validation_split=binding),tmp_path/'outside',mapper)
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='receipt changed'):
        dataset_split(dict(validation_split=binding),tmp_path,mapper)


def test_extension_bank_denied_without_explicit_generation_split(tmp_path):
    from research.rl_trading.v6.session_data import open_session
    root=tmp_path/'bankday';root.mkdir()
    (root/'plan.json').write_text(json.dumps(dict(day='2026-08-26',validation_split={})))
    (root/'complete.json').write_text('{}')
    with pytest.raises(ValueError,match='explicit generation-only'):
        open_session(root,runtime_root=tmp_path)


def test_1b_extension_audit_accepts_exact_eight_days_and_rejects_missing_or_changed_roles():
    from research.rl_trading.v6.market_teacher_dataset import validate_population
    import copy
    original=dict(context=dict(day='2026-08-25',role='development'),days=[dict(day=d,role='sealed_test' if i<3 else 'development') for i,d in enumerate(DAYS)],validation_split=dict(hash='pinned'))
    dataset=dict(days=[original['context']]+original['days'],validation_split=original['validation_split'])
    validate_population(dataset,original)
    missing=copy.deepcopy(dataset);missing['days'].pop()
    with pytest.raises(ValueError):validate_population(missing,original)
    changed=copy.deepcopy(dataset);changed['days'][1]['role']='development'
    with pytest.raises(ValueError):validate_population(changed,original)
    changed=copy.deepcopy(dataset);changed['validation_split']['hash']='other'
    with pytest.raises(ValueError):validate_population(changed,original)
