from copy import deepcopy
import os

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.backend import level_reaction_service as service
from research.reaction_levels.v1 import inference


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


def test_winner_is_directional_break_not_rejection():
    results = [dict(side='upper', level=dict(id='a'), probabilities=dict(broken=.36,rejected=.27)),
               dict(side='lower', level=dict(id='b'), probabilities=dict(broken=.15,rejected=.52))]
    assert inference.winner(results)['direction']=='up'
    results[1]['probabilities']['broken']=.42
    assert inference.winner(results)['probability']==.42
    results[0]['probabilities']['broken']=.42
    assert inference.winner(results)['direction']=='up'
    assert inference.winner([]) is None


@pytest.mark.skipif(not os.environ.get('REACTION_TEST_MODEL'),reason='Requires completed model artifacts')
def test_series_strategy_parity_rewind_and_exact_closes():
    from datetime import datetime, timezone
    from src.trading_runtime.level_reaction import LevelReactionFeed
    from src.trading_runtime.strategy_engine import StrategyObservation
    model_id=os.environ['REACTION_TEST_MODEL']
    request=service.PredictionRequest(model_id=model_id,ticker='SUGP',session_date='2026-08-21',time_et='07:10:46')
    stamp=int(datetime.fromisoformat('2026-08-21T07:10:46-04:00').timestamp())
    closes=list(range(stamp-300,stamp+1))
    app=FastAPI();app.include_router(service.router);client=TestClient(app)
    response=client.post('/api/research/level-reaction/series',json=dict(request.model_dump(mode='json'),close_times=closes))
    assert response.status_code==200, response.text
    batch=response.json()
    assert len(batch['records'])==len(closes)
    assert batch['records'][-1]['winner']['direction']=='up'
    assert batch['records'][-1]['winner']['probability']==pytest.approx(.3627049057)
    observation=StrategyObservation(ticker='SUGP',observed_at=datetime.fromtimestamp(stamp,timezone.utc),price=4.48)
    feed=LevelReactionFeed(model_id)
    enriched=feed.enrich(observation,stamp)
    assert observation.reaction_prediction is None
    for key,value in batch['records'][-1].items():
        assert enriched.reaction_prediction[key]==value
    with pytest.raises(ValueError,match='stale or future'):
        feed.enrich(observation,stamp-1)
    with pytest.raises(ValueError,match='stale or future'):
        feed.enrich(observation,stamp+1)
    earlier=request.model_copy(update=dict(time_et='07:10:40'))
    rewind=inference.predict_series(earlier,[stamp-6])
    assert rewind['records'][0]==batch['records'][-7]
    with pytest.raises(ValueError,match='completed seconds'):
        inference.predict_series(earlier,[stamp])
    five=inference.predict_series(request,[stamp-1],timeframe_seconds=5)['records'][0]
    assert five['results']==batch['records'][-2]['results']
    assert five['candle_start']==stamp-6 and five['available_at']==stamp-1
    # Mutating a returned record must not corrupt the shared cache.
    five['results'].clear()
    assert inference.predict_series(request,[stamp-1])['records'][0]['results']
    frozen=pd.read_parquet(service.ROOT/model_id/'test-predictions.parquet')
    for record in batch['records'][::37]:
        for result in record['results']:
            row=frozen[(frozen.t==record['as_of']) & (frozen.target_upper==int(result['side']=='upper'))].iloc[0]
            np.testing.assert_allclose(list(result['probabilities'].values()),[row['p_'+label] for label in service.CONTRACT['labels']],rtol=1e-7,atol=1e-9)


@pytest.mark.skipif(not os.environ.get('REACTION_TEST_MODEL'),reason='Requires completed model artifacts')
def test_cached_features_match_real_prefix_and_artifact_change_fails_closed(monkeypatch):
    from datetime import datetime
    request=service.PredictionRequest(model_id=os.environ['REACTION_TEST_MODEL'],ticker='SUGP',session_date='2026-08-21',time_et='07:10:46')
    stamp=int(datetime.fromisoformat('2026-08-21T07:10:46-04:00').timestamp())
    inference.predict_series(request,[stamp])
    session=inference._sessions[(str(inference.ROOT),request.model_id,'SUGP',request.session_date)][1]
    for end in (stamp-600,stamp):
        rows,features,_,_=inference.feature_rows(inference.prefix(session['inputs'],end),session['effective'])
        prefix_rows=rows[rows.t==end].sort_values('target_upper')[features]
        cached=session['rows'];cached=cached[cached.t==end].sort_values('target_upper')[features]
        np.testing.assert_allclose(prefix_rows.to_numpy(),cached.to_numpy(),equal_nan=True,rtol=0,atol=0)
    original=inference._signature
    monkeypatch.setattr(inference,'_signature',lambda req: original(req)+(('changed',),))
    def reject(_):
        raise ValueError('Frozen model integrity check failed')
    monkeypatch.setattr(inference,'load_session',reject)
    with pytest.raises(ValueError,match='integrity'):
        inference.predict_series(request,[stamp])
