import json
import pytest
from research.vectorized_backtest.v61.resume_authority import global_predecessor
from research.vectorized_backtest.v61.distributed_assets import sha


def test_implementation_deployment_can_change_but_objective_cannot(tmp_path):
    old=dict(source_sha256='old',code='old',commit='old',deployment='old-receipt',
             machines={'workstation':{'code':'old'}},objective={'drawdown_weight':1.})
    path=tmp_path/'predecessor.json';path.write_text(json.dumps(old))
    job=dict(old,source_sha256='new',code='new',commit='new',deployment='new-receipt',
             machines={'workstation':{'code':'new'}},
             resume_transition=dict(predecessor_job=str(path),predecessor_job_sha256=sha(path)))
    assert global_predecessor(job)==old
    with pytest.raises(ValueError,match='campaign semantics'):
        global_predecessor(dict(job,objective={'drawdown_weight':0.}))
