import copy
import json
import pytest
import torch
from research.rl_trading.v1.common import digest
from research.rl_trading.v6.run_ranked_multisession_underfit import validate_continuation


def setup_contract(tmp_path):
    plan = dict(version='rl-v6-ranked-six-session-underfit-v1', epochs=400,
        sealed_labels_read=False, development_labels_read=False,
        generalization_evaluated=False, workstation_gpu_used=False,
        width=512, selection_sha256='selection', normalization_sha256='normalizer',
        source_files_sha256={'run_ranked_multisession_underfit.py':
            'e90f8d6f679d715b1d2df0e4b7a23a58c29187e4513a60f8138be3f272d0b101',
            'training.py':'calculation'})
    plan['hash'] = digest(plan)
    (tmp_path/'manifest.json').write_text(json.dumps(plan))
    (tmp_path/'metrics.jsonl').write_text(json.dumps(dict(epoch=85, metrics={'f1':1}))+'\n')
    torch.save(dict(epoch=85,model={},optimizer={}),tmp_path/'epoch-085.pt')
    return plan


def test_preserved_checkpoint_bound_to_evaluation(tmp_path):
    plan=setup_contract(tmp_path)
    checkpoint, record, receipt=validate_continuation(tmp_path,85,plan)
    assert checkpoint.name=='epoch-085.pt' and record['epoch']==85
    assert receipt['checkpoint_sha256'] and receipt['parent_manifest_sha256']
    assert receipt['exact_uninterrupted_resume'] is False


@pytest.mark.parametrize('field,value', [('width',128),('selection_sha256','other'),('normalization_sha256','other')])
def test_reject_changed_contract(tmp_path,field,value):
    plan=setup_contract(tmp_path);plan[field]=value
    with pytest.raises(ValueError,match='contract changed'):
        validate_continuation(tmp_path,85,plan)


def test_reject_changed_calculation_and_unreviewed_runner(tmp_path):
    plan=setup_contract(tmp_path);new=copy.deepcopy(plan)
    new['source_files_sha256']['training.py']='new'
    with pytest.raises(ValueError,match='calculation source'):
        validate_continuation(tmp_path,85,new)
    old=copy.deepcopy(plan);old.pop('hash');old['source_files_sha256']['run_ranked_multisession_underfit.py']='unknown'
    old['hash']=digest(old);(tmp_path/'manifest.json').write_text(json.dumps(old))
    with pytest.raises(ValueError,match='Unreviewed'):
        validate_continuation(tmp_path,85,plan)


def test_reject_unaudited_epoch_or_manifest(tmp_path):
    plan=setup_contract(tmp_path)
    with pytest.raises(ValueError,match='saved evaluation'):
        validate_continuation(tmp_path,90,plan)
    old=json.loads((tmp_path/'manifest.json').read_text());old['width']=128
    (tmp_path/'manifest.json').write_text(json.dumps(old))
    with pytest.raises(ValueError,match='manifest hash'):
        validate_continuation(tmp_path,85,plan)
