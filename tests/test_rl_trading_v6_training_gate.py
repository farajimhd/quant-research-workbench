import json
import pytest
from research.rl_trading.v1.common import digest
from research.rl_trading.v6.opportunity_dataset import VERSION, ALGORITHM
from research.rl_trading.v6.training_gate import require_dataset


def test_partial_forward_audit_cannot_launch_training(tmp_path):
    data={'version':VERSION,'algorithm':ALGORITHM,'status':'audited_ready_for_training',
          'sealed_test_accessed':False,'days':[],
          'rank_exclusion_policy':'ignore_entire_entry_and_recompile_account'}
    data['hash']=digest(data)
    path=tmp_path/'complete.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='complete new opportunity dataset'):
        require_dataset(path,runtime_root=tmp_path)


def test_altered_audit_digest_cannot_launch_training(tmp_path):
    path=tmp_path/'complete.json'
    path.write_text(json.dumps({'version':VERSION,'status':'audited_ready_for_training',
        'sealed_test_accessed':False,'hash':'changed'}))
    with pytest.raises(ValueError,match='complete new opportunity dataset'):
        require_dataset(path,runtime_root=tmp_path)
