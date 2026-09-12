from copy import deepcopy
import os

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.backend import level_reaction_service as service


def test_prefix_removes_future_quotes_bars_and_profile():
    inputs = dict(source=dict(start='2026-08-21T04:00:00-04:00'),
        bars=[dict(t=1),dict(t=2)],quotes=[dict(t=1),dict(t=3)],profile=['future'])
    original=deepcopy(inputs)
    result=service.prefix(inputs,1)
    assert result['bars']==[dict(t=1)] and result['quotes']==[dict(t=1)]
    assert 'profile' not in result and inputs==original


def test_request_rejects_paths_and_busy():
    app=FastAPI();app.include_router(service.router);client=TestClient(app)
    request=dict(model_id='../outside',ticker='SUGP',session_date='2026-08-21',time_et='07:10:46')
    assert client.post('/api/research/level-reaction/predict',json=request).status_code==422
    request['model_id']='safe'
    with service._busy:
        assert client.post('/api/research/level-reaction/predict',json=request).status_code==429


@pytest.mark.skipif(not os.environ.get('REACTION_TEST_MODEL'),reason='Requires an explicitly selected completed model artifact')
def test_real_inference_matches_frozen_batch_and_respects_cutoff():
    model_id=os.environ['REACTION_TEST_MODEL'];root=service.ROOT/model_id
    manifest=service.read(root/'manifest.json')
    request=service.PredictionRequest(model_id=model_id,ticker=manifest['ticker'],session_date=manifest['test_day'],time_et='07:10:46')
    result=service.calculate(request)
    batch=pd.read_parquet(root/'test-predictions.parquet')
    batch=batch[batch.t==result['as_of']]
    for item in result['results']:
        row=batch[(batch.target_upper==int(item['side']=='upper')) & (batch.level_id==item['level']['id'])].iloc[0]
        np.testing.assert_allclose(list(item['probabilities'].values()),[row['p_'+label] for label in service.CONTRACT['labels']],rtol=1e-7,atol=1e-9)
    assert result['max_input_timestamp']<=result['as_of']
    assert all(c['t']<=result['as_of'] for c in result['candles'])
    with pytest.raises(ValueError,match='cutoff'):
        service.calculate(request.model_copy(update=dict(session_date=request.session_date.fromisoformat(manifest['calibration_days'][-1]))))
    with pytest.raises(ValueError,match='ticker'):
        service.calculate(request.model_copy(update=dict(ticker='OTHER')))
