import copy
import pytest
from research.vectorized_backtest.v65.resume_authority import global_predecessor, accept_checkpoint
from research.vectorized_backtest.v65.publication import write_json
from research.vectorized_backtest.v65.distributed_assets import sha
from research.vectorized_backtest.v65.identity import digest


def fixture(tmp_path, completed=158):
    old=dict(version='v65-global-v1',source_sha256='old',phase2_generations=200,
             machines={'workstation':{'code':'old'}},selection={'elite':.1})
    path=tmp_path/'old.json';write_json(path,old)
    state=dict(completed=completed,seals={'1':'unchanged'},rng={'state':123},agent_reviews={'155':'review'})
    contract=dict(source_sha256='old',generations=200,selection=old['selection'])
    saved=dict(contract=contract,state=state,state_sha256=digest(state))
    cp=tmp_path/'checkpoint.json';write_json(cp,saved)
    job=dict(old,source_sha256='new',phase2_generations=300,resume_transition=dict(
        predecessor_job=str(path),predecessor_job_sha256=sha(path),generation_extension={'from':200,'to':300},
        global_checkpoint=dict(checkpoint_sha256=sha(cp),state_sha256=digest(state),contract_sha256=digest(contract))))
    return job,cp,saved,dict(contract,source_sha256='new',generations=300)


def test_extension_preserves_checkpoint_and_rejects_unrelated_changes(tmp_path):
    job,cp,saved,expected=fixture(tmp_path);before=cp.read_bytes()
    accept_checkpoint(job,cp,saved,expected)
    assert cp.read_bytes()==before
    for change in ({'selection':{'elite':.5}}, {'phase2_generations':400}):
        with pytest.raises(ValueError):global_predecessor(dict(job,**change))
    bad=copy.deepcopy(job);del bad['resume_transition']['generation_extension']
    with pytest.raises(ValueError):global_predecessor(bad)
    with pytest.raises(ValueError):accept_checkpoint(job,cp,saved,dict(expected,selection={'elite':.5}))
    cp.write_bytes(before+b' ')
    with pytest.raises(ValueError,match='bound receipt'):accept_checkpoint(job,cp,saved,expected)


def test_terminal_checkpoint_cannot_extend_without_successor(tmp_path):
    job,cp,saved,expected=fixture(tmp_path,completed=200)
    with pytest.raises(ValueError,match='unfinished'):accept_checkpoint(job,cp,saved,expected)
