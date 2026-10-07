import json
import os
import shutil
from pathlib import Path

import pytest

from research.rl_trading.v1.common import digest
from research.vectorized_backtest.v5.torch_backtest.feature_bank import SCALARS, LEVELS
from research.vectorized_backtest.v5.torch_backtest.input_audit import audit_inputs, FreshHashes, verify_session, main
from research.vectorized_backtest.v5.torch_backtest.runtime import file_hash


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def campaign(tmp_path):
    """Tiny byte fixtures isolate input auditing from the prior financial audit."""
    root = tmp_path / 'run'
    sessions = []
    bindings = []
    for index in range(30):
        day = f'2026-07-{index+1:02d}'
        bank = tmp_path / 'banks' / day
        plan = {'day': day}
        plan['hash'] = digest(plan)
        put(bank / 'plan.json', plan)
        files = {}
        for name in ('close_us.npy', 'scalar.npy', 'levels.npy'):
            path = bank / 'bank' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((day + name).encode())
            files[name] = file_hash(path)
        put(bank / 'bank/complete.json', dict(source_hash=plan['hash'], scalar_names=SCALARS,
                                             level_names=LEVELS, files_sha256=files))
        put(bank / 'complete.json', dict(status='complete',plan_hash=plan['hash'],bank_file_hashes=files))
        execution = tmp_path / 'execution' / day
        execution.mkdir(parents=True)
        (execution / 'tape.pt').write_bytes(day.encode())
        tape_hash = file_hash(execution / 'tape.pt')
        put(execution / 'receipt.json', dict(identity={'producer': 'fixture'},source_fingerprint=day,sha256=tape_hash))
        mapping = put(tmp_path / 'maps' / (day+'.json'),dict(bank_certificate_sha256=file_hash(bank/'complete.json')))
        split = put(tmp_path / 'splits' / (day+'.json'),{'as_of': day})
        sessions.append(dict(day=day,feature_root=str(bank),execution_root=str(execution),
                             identity_map=str(mapping),split_certificate=str(split)))
        bindings.append(dict(day=day,feature_certificate=file_hash(bank/'complete.json'),prior_certificate=None,
                             identity_map_sha256=file_hash(mapping),split_certificate_sha256=file_hash(split),
                             previous_split_certificate_sha256=None,execution=dict(receipt_sha256=file_hash(execution/'receipt.json'),
                             tape_sha256=tape_hash,creator_identity={'producer':'fixture'},source_fingerprint=day)))
    put(root/'identity.json',dict(code_hash='fixture-source',arguments=dict(generations=2,profile=False),
                                 sessions=dict(training=sessions,validation=[{'day':f'2026-08-{i+1:02d}'} for i in range(6)])))
    generations = []
    for generation_index in range(2):
        folder = root/f'generation_{generation_index:03d}'
        receipts = []
        for index,binding in enumerate(bindings):
            path = put(folder/f'session_{index:03d}/receipt.json',dict(**binding,population_sha256='population'))
            receipts.append(dict(path=str(path),sha256=file_hash(path)))
        path = put(folder/'generation.json',dict(receipts=receipts,population_sha256='population'))
        generations.append(dict(path=str(path),sha256=file_hash(path)))
    put(root/'checkpoint.json',dict(next_generation=2))
    put(root/'audit.json',dict(status='passed',full_budget_verified=True,completed_generations_verified=2,
                              identity_sha256=file_hash(root/'identity.json'),checkpoint_sha256=file_hash(root/'checkpoint.json'),
                              generation_bindings=generations))
    return root, sessions


def test_rehashes_all_consumed_training_files_without_opening_validation(tmp_path):
    root,sessions = campaign(tmp_path)
    assert main(['--output',str(root)]) == 0
    report = json.loads((root/'training_input_integrity.json').read_text())
    assert report['training_sessions_verified'] == 30
    assert report['validation_sessions_verified'] == 0 and not report['validation_opened']
    assert len({row['path'] for row in report['files']}) == len(report['files'])
    assert any(row['path'].endswith('scalar.npy') for row in report['files'])
    assert report['kind'] == 'supplemental-consumed-input-byte-audit'


def test_prior_bank_and_split_are_hashed_and_future_prior_is_rejected(tmp_path):
    root,sessions = campaign(tmp_path)
    spec=dict(sessions[1],previous_feature_root=sessions[0]['feature_root'],
              previous_split_certificate=sessions[0]['split_certificate'])
    receipt=json.loads((root/'generation_000/session_001/receipt.json').read_text())
    receipt['prior_certificate']=file_hash(Path(sessions[0]['feature_root'])/'complete.json')
    receipt['previous_split_certificate_sha256']=file_hash(sessions[0]['split_certificate'])
    hashes=FreshHashes();verify_session(spec,receipt,hashes)
    assert str((Path(sessions[0]['feature_root'])/'bank/scalar.npy').resolve()) in hashes.files
    assert str(Path(sessions[0]['split_certificate']).resolve()) in hashes.files
    spec['previous_feature_root']=sessions[1]['feature_root']
    receipt['prior_certificate']=file_hash(Path(sessions[1]['feature_root'])/'complete.json')
    with pytest.raises(ValueError,match='current/future session'):
        verify_session(spec,receipt,FreshHashes())


@pytest.mark.parametrize('artifact',['scalar.npy','tape.pt'])
def test_detects_changed_bytes_even_with_preserved_size_and_mtime(tmp_path,artifact):
    root,sessions = campaign(tmp_path)
    session = sessions[0]
    path = Path(session['feature_root'])/'bank/scalar.npy' if artifact=='scalar.npy' else Path(session['execution_root'])/'tape.pt'
    stamp = path.stat()
    original = path.read_bytes()
    path.write_bytes(b'X'+original[1:])
    os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    with pytest.raises(ValueError,match='byte hash mismatch'):
        audit_inputs(root)
    assert not (root/'training_input_integrity.json').exists()


def test_cross_generation_binding_change_rejected_even_with_consistent_receipt_hashes(tmp_path):
    root,_ = campaign(tmp_path)
    receipt_path=root/'generation_001/session_000/receipt.json'
    receipt=json.loads(receipt_path.read_text());receipt['feature_certificate']='different';put(receipt_path,receipt)
    generation_path=root/'generation_001/generation.json'
    generation=json.loads(generation_path.read_text());generation['receipts'][0]['sha256']=file_hash(receipt_path);put(generation_path,generation)
    audit=json.loads((root/'audit.json').read_text());audit['generation_bindings'][1]['sha256']=file_hash(generation_path);put(root/'audit.json',audit)
    with pytest.raises(ValueError,match='binding changed across generations'):
        audit_inputs(root)


def test_unfinished_budget_and_unfrozen_validation_fail_before_bank_reads(tmp_path):
    root,sessions = campaign(tmp_path)
    audit=json.loads((root/'audit.json').read_text());audit['full_budget_verified']=False;put(root/'audit.json',audit)
    with pytest.raises(ValueError,match='full-budget training audit'):
        audit_inputs(root)
    audit['full_budget_verified']=True;put(root/'audit.json',audit)
    with pytest.raises(ValueError,match='completed frozen evaluation'):
        audit_inputs(root,include_validation=True)


def test_live_owner_blocks_expensive_byte_scan(tmp_path):
    root,_ = campaign(tmp_path)
    (root/'owner.lock').write_text('live worker')
    with pytest.raises(ValueError,match='released financial worker ownership'):
        audit_inputs(root)


def test_completed_final_inputs_require_and_match_six_audited_receipt_hashes(tmp_path):
    root,training = campaign(tmp_path)
    identity=json.loads((root/'identity.json').read_text())
    final_bindings=[]
    for index,item in enumerate(identity['sessions']['validation']):
        day=item['day'];old=training[index]
        bank=tmp_path/'final-banks'/day;execution=tmp_path/'final-execution'/day
        shutil.copytree(old['feature_root'],bank);shutil.copytree(old['execution_root'],execution)
        plan=json.loads((bank/'plan.json').read_text());plan['day']=day
        plan['hash']=digest({k:v for k,v in plan.items() if k!='hash'});put(bank/'plan.json',plan)
        manifest=json.loads((bank/'bank/complete.json').read_text());manifest['source_hash']=plan['hash'];put(bank/'bank/complete.json',manifest)
        certificate=json.loads((bank/'complete.json').read_text());certificate['plan_hash']=plan['hash'];put(bank/'complete.json',certificate)
        mapping=put(tmp_path/'final-maps'/(day+'.json'),dict(bank_certificate_sha256=file_hash(bank/'complete.json')))
        split=put(tmp_path/'final-splits'/(day+'.json'),{'as_of':day})
        item.update(feature_root=str(bank),execution_root=str(execution),identity_map=str(mapping),split_certificate=str(split))
        producer=json.loads((execution/'receipt.json').read_text())
        final_bindings.append(dict(day=day,execution=dict(receipt_sha256=file_hash(execution/'receipt.json'),
            tape_sha256=producer['sha256'],creator_identity=producer['identity'],source_fingerprint=producer['source_fingerprint']),
            feature_certificate=file_hash(bank/'complete.json'),prior_certificate=None,
            identity_map_sha256=file_hash(mapping),split_certificate_sha256=file_hash(split),previous_split_certificate_sha256=None))
    put(root/'identity.json',identity)
    freeze=put(root/'frozen_winner.json',dict(identity_sha256=file_hash(root/'identity.json')))
    receipts=[]
    for index,binding in enumerate(final_bindings):
        path=put(root/f'validation_{index:03d}/receipt.json',dict(**binding,freeze_sha256=file_hash(freeze)))
        receipts.append(dict(path=str(path),sha256=file_hash(path)))
    audit=json.loads((root/'audit.json').read_text())
    audit.update(identity_sha256=file_hash(root/'identity.json'),freeze_sha256=file_hash(freeze),
                 validation_sessions_verified=6,validation_bindings=receipts)
    put(root/'audit.json',audit);put(root/'report.json',dict(status='completed'))
    report=audit_inputs(root,include_validation=True)
    assert report['validation_sessions_verified']==6 and report['validation_opened']
    assert report['simulation_code_hash']=='fixture-source'
    path=root/'validation_000/receipt.json'
    receipt=json.loads(path.read_text());receipt['identity_map_sha256']='changed';put(path,receipt)
    with pytest.raises(ValueError,match='byte hash mismatch'):
        audit_inputs(root,include_validation=True)
