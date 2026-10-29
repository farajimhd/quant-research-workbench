import json
from pathlib import Path
import numpy as np
import pytest
from research.vectorized_backtest.v58.robust_score import concentration,ticker_costs
from research.vectorized_backtest.v58.founder_restart import engine_hashes,verify_binding
from research.vectorized_backtest.v42.distributed_assets import sha


def test_sparse_profit_and_full_losses():
    assert ticker_costs([100.],[0])[1].tolist()==[100.]
    assert ticker_costs([-100.],[0])[1].tolist()==[-200.]
    assert ticker_costs([],[])[1].tolist()==[]
    assert ticker_costs([100.,20.],[0,0])[1].tolist()==[95.]
    assert ticker_costs([100.,20.],[0,1])[1].tolist()==[100.,20.]
    np.testing.assert_allclose(concentration([100.,100.,100.],[1,2,3]),[0.,25.,100/3])
    with pytest.raises(ValueError):concentration(100,2,1.1)


def test_replay_engine_and_limits_identical_to_v57():
    root=Path(__file__).resolve().parents[1]
    assert engine_hashes(root,'v57')==engine_hashes(root,'v58')


def test_restart_rejects_changed_campaign_or_tampered_binding(tmp_path):
    root=Path(__file__).resolve().parents[1]
    old=dict(campaign=str(tmp_path/'v57'),population=1024,execution={'risk_limits_enabled':False},machines={'workstation':{'code':'old','workers':2}})
    prior=tmp_path/'job.json';prior.write_text(json.dumps(old))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(dict(job=str(prior),job_sha256=sha(prior),engine_hashes=engine_hashes(root,'v57'))))
    job=dict(old,campaign=str(tmp_path/'v58'),code=str(root),founder_restart=dict(manifest=str(manifest),sha256=sha(manifest)))
    verify_binding(job)
    with pytest.raises(ValueError,match='semantics'):verify_binding(dict(job,population=512))
    manifest.write_text('{}')
    with pytest.raises(ValueError,match='source changed'):verify_binding(job)
