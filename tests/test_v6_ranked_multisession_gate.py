import json
import pytest
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.ranked_multisession_gate import admit_multisession
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.ranked_normalization import VERSION
from research.rl_trading.v6.execution_features import CANDLE_NORMALIZATION_VERSION
from test_v6_ranked_multisession_metrics import report


def fixture(tmp_path):
    def write(name,r): (tmp_path/name).write_text(json.dumps(r))
    reports=[]
    for count in (6,6,5,5,5,5):
        r=report();r['action_class_counts']=dict.fromkeys(r['action_class_counts'],count)
        r['action_predicted_class_counts']=dict.fromkeys(r['action_class_counts'],count);reports.append(r)
    metrics=pool_gate_metrics(reports)
    write('normalization.json',dict(version=VERSION,scope='train_only'))
    (tmp_path/'last.pt').write_bytes(b'fixture checkpoint, never trained')
    (tmp_path/'producer.py').write_text('# fixture producer')
    plan=dict(version='rl-v6-ranked-six-session-underfit-v1',sessions=[dict(day=str(i)) for i in range(6)],
        input_population_preserved=True,sealed_labels_read=False,development_labels_read=False,
        workstation_gpu_used=False,generalization_evaluated=False,teacher_loss='branch-balanced-v3',
        regression_weights=[0.,0.],auxiliary_weights=dict(ratio=1.,forecast=1.,quality=1.,future_quality=1.),
        normalization_sha256=file_hash(tmp_path/'normalization.json'),feature_contract=CANDLE_NORMALIZATION_VERSION,
        source_files_sha256={'producer.py':file_hash(tmp_path/'producer.py')})
    plan['hash']=digest(plan);write('manifest.json',plan)
    write('complete.json',dict(status='completed',passed=True,reload_exact=True,generalization_evaluated=False,
        epoch=100,metrics=metrics,checkpoint_sha256=file_hash(tmp_path/'last.pt')))
    write('result.json',dict(passed=True,epoch=100,metrics=metrics,sessions=reports))
    return write


def test_gate_requires_same_epoch_head_evidence(tmp_path):
    write=fixture(tmp_path);admit_multisession(tmp_path,tmp_path)
    r=json.loads((tmp_path/'result.json').read_text());r['epoch']=99;write('result.json',r)
    with pytest.raises(ValueError,match='same-checkpoint'):admit_multisession(tmp_path,tmp_path)


@pytest.mark.parametrize('name',['last.pt','normalization.json','producer.py'])
def test_modified_checkpoint_normalization_or_producer_denies_admission(tmp_path,name):
    fixture(tmp_path)
    if name.endswith('.json'):(tmp_path/name).write_text(json.dumps(dict(version=VERSION,scope='train_only',changed=True)))
    else:(tmp_path/name).write_bytes(b'changed')
    with pytest.raises(ValueError):admit_multisession(tmp_path,tmp_path)


def test_missing_auxiliary_or_cross_session_evidence_denies_admission(tmp_path):
    write=fixture(tmp_path);r=json.loads((tmp_path/'result.json').read_text())
    r['sessions'][0]['allocation_ratio_mae']=.2;r['sessions'][0]['allocation_error_sum']=2.;write('result.json',r)
    with pytest.raises(ValueError,match='same-checkpoint'):admit_multisession(tmp_path,tmp_path)


@pytest.mark.parametrize('width,argument,allowed', [(512,512,True),(512,128,False),(256,256,False),(True,True,False)])
def test_capacity_is_bound_to_underfit_arguments(tmp_path,width,argument,allowed):
    write=fixture(tmp_path);plan=json.loads((tmp_path/'manifest.json').read_text())
    plan.update(width=width,arguments=dict(width=argument));plan.pop('hash');plan['hash']=digest(plan)
    write('manifest.json',plan)
    if allowed:
        admitted,_,_=admit_multisession(tmp_path,tmp_path)
        assert admitted['width']==512
    else:
        with pytest.raises(ValueError,match='model width'):admit_multisession(tmp_path,tmp_path)


@pytest.mark.parametrize('flag,argument,allowed',[(True,True,True),(False,False,True),(True,False,False),(1,1,False)])
def test_checkpointing_is_bound_to_underfit_arguments(tmp_path,flag,argument,allowed):
    write=fixture(tmp_path);plan=json.loads((tmp_path/'manifest.json').read_text())
    plan.update(activation_checkpointing=flag,arguments=dict(activation_checkpointing=argument))
    plan.pop('hash');plan['hash']=digest(plan);write('manifest.json',plan)
    if allowed:admit_multisession(tmp_path,tmp_path)
    else:
        with pytest.raises(ValueError,match='Activation checkpointing'):admit_multisession(tmp_path,tmp_path)


@pytest.mark.parametrize('flag,argument,allowed',[(True,True,True),(False,False,True),(True,False,False),(1,1,False)])
def test_offloading_is_bound_to_underfit_arguments(tmp_path,flag,argument,allowed):
    write=fixture(tmp_path);plan=json.loads((tmp_path/'manifest.json').read_text())
    plan.update(cpu_saved_tensors=flag,arguments=dict(cpu_saved_tensors=argument))
    plan.pop('hash');plan['hash']=digest(plan);write('manifest.json',plan)
    if allowed:admit_multisession(tmp_path,tmp_path)
    else:
        with pytest.raises(ValueError,match='Saved tensor offloading'):admit_multisession(tmp_path,tmp_path)
