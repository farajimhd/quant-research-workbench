import copy
import json
import numpy as np
import pytest
from research.vectorized_backtest.v63.robust_score import combine,VERSION
from research.vectorized_backtest.v63.objectives import Objective
from research.vectorized_backtest.v63.identity import digest
from research.vectorized_backtest.v63.distributed_assets import sha
from research.vectorized_backtest.v63.resume_authority import accept_checkpoint,global_predecessor


def test_drawdown_is_diagnostic_only_and_penalty_remains():
    panel={k:np.zeros(2) for k in ('gross_losses','maximum_position_risk')}
    panel.update(net_pnl=np.array([100.,100.]),ticker_component=np.array([100.,100.]),
        drawdown=np.array([0.,100000.]),closed_positions=np.ones(2),winning_positions=np.ones(2),
        blocked_requests=np.array([2.,2.]),eligible=np.ones(2,dtype=bool))
    result=combine([panel,panel])
    np.testing.assert_array_equal(result['score'],[180.,180.])
    np.testing.assert_array_equal(result['uncapped_score'],[200.,200.])
    np.testing.assert_array_equal(result['drawdown'],[0.,200000.])
    assert Objective().validate().drawdown_weight==0
    with pytest.raises(ValueError):Objective(drawdown_weight=1).validate()


def transition(tmp_path):
    old=dict(version='v63-global-v1',source_sha256='old',objective=dict(drawdown_weight=1.,loss_weight=0.),
             score_version='log-profit-target-blocked-request-v2',machines={'workstation':{'code':'old'}},execution={'unchanged':True})
    path=tmp_path/'old-job.json';path.write_text(json.dumps(old))
    job=copy.deepcopy(old);job.update(source_sha256='new',objective=dict(drawdown_weight=0.,loss_weight=0.),score_version=VERSION)
    state=dict(completed=-1,seals={});contract={k:old[k] for k in ('source_sha256','objective','score_version','execution')}
    saved=dict(state=state,state_sha256=digest(state),contract=contract)
    checkpoint=tmp_path/'checkpoint.json';checkpoint.write_text(json.dumps(saved))
    job['resume_transition']=dict(predecessor_job=str(path),predecessor_job_sha256=sha(path),
        score_change=dict(from_version=old['score_version'],to_version=VERSION,initial_only=True),
        global_checkpoint=dict(checkpoint_sha256=sha(checkpoint),state_sha256=digest(state),contract_sha256=digest(contract)))
    expected={k:job[k] for k in contract}
    return job,checkpoint,saved,expected


def test_initial_objective_transition_is_narrow_and_receipt_bound(tmp_path):
    job,path,saved,expected=transition(tmp_path)
    accept_checkpoint(job,path,saved,expected)
    bad=copy.deepcopy(job);bad['execution']['unchanged']=False
    with pytest.raises(ValueError):global_predecessor(bad)
    with pytest.raises(ValueError):accept_checkpoint(job,path,saved,dict(expected,population=5))


def test_objective_transition_cannot_keep_a_successor_selected_by_old_score(tmp_path):
    job,path,saved,expected=transition(tmp_path)
    saved['state']['completed']=0;saved['state_sha256']=digest(saved['state'])
    path.write_text(json.dumps(saved))
    job['resume_transition']['global_checkpoint'].update(checkpoint_sha256=sha(path),state_sha256=saved['state_sha256'])
    with pytest.raises(ValueError,match='unselected initial founders'):accept_checkpoint(job,path,saved,expected)
