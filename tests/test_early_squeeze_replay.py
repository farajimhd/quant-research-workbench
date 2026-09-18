import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta, time
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, ReplaySignalEvent, _occurrence_source_values
from tests.test_replay_run_service import approved_configuration
from tests.test_early_squeeze_breakout import fixture, NOW
from src.trading_runtime import early_squeeze_breakout as E


@pytest.mark.parametrize('single_ticker',[False,True])
@pytest.mark.parametrize('start_hour',[4,9])
def test_first_occurrence_availability_is_identical_for_named_and_session_runs(tmp_path,single_ticker,start_hour):
    async def run():
        host,a,obs=fixture()
        configuration=approved_configuration()
        configuration['payload']['strategy']['parameters']=deepcopy(a.parameters)
        configuration['payload']['run_plan']=dict(enabled=True,signal_stream_ids=['price-squeeze-early'],
            enablement=dict(state='enabled',scope='persistent'),
            watchlist_ids=[],activation=dict(event_policy='latest_session_occurrence',watchlist_policy='not_required',watch_duration='session'))
        configuration['payload']['signal_activation']=dict(signal_streams=[dict(signal_stream_id='price-squeeze-early',
            enabled=True,occurrence_source='qmd_squeeze_episode')])
        controller=ReplayRunController(ReplayRunDefinition(session_date=NOW.date(),start_time=time(start_hour),
            tickers=(a.ticker,) if single_ticker else (),configuration_revision=configuration),runtime_root=tmp_path)
        occurrence=dict(ticker=a.ticker,signal_stream_id='price-squeeze-early',event_id='first',last_price=10.39,
            effective_at=(NOW-timedelta(seconds=10)).isoformat(),available_at=NOW.isoformat())
        event=ReplaySignalEvent(available_at=NOW,ticker=a.ticker,occurrence=occurrence,
                               source_values=_occurrence_source_values(occurrence))
        assert not controller._signal_activated_tickers
        assert not host.evaluate(a,obs(-1,signal=False)).evaluation.intents
        with patch.object(controller,'_after_event',new_callable=AsyncMock):
            await controller._process_external_signal_event(event)
            assert a.ticker in controller._signal_activated_tickers
            await controller._process_external_signal_event(replace(event,available_at=NOW+timedelta(hours=1),
                source_values={E.SIGNAL:dict(value=True,observed_at=(NOW+timedelta(hours=1)).isoformat(),event_id='later')}))
            controller._refresh_source_native_signal_activation(NOW+timedelta(hours=5))
        saved=controller._strategy_source_values[a.ticker][E.SIGNAL]
        assert saved['observed_at']==NOW.isoformat() and saved['event_id']=='first'
        assert a.ticker in controller._signal_activated_tickers
    asyncio.run(run())


def test_passive_replay_context_uses_filtered_levels_without_running_old_strategy(tmp_path):
    from types import SimpleNamespace
    from src.backend.replay_run_service import ReplayDerivedFrame
    async def run():
        _,a,obs=fixture();o=obs()
        controller=ReplayRunController.__new__(ReplayRunController)
        controller.definition=SimpleNamespace(configuration_revision={'payload':{'strategy':{'parameters':a.parameters}}},
                                               experimental_structure_book='filtered-v7')
        controller._candle_detector_states={}
        controller._structural_market_streams={}
        controller._recovery_book_identity=dict(id='filtered-v7',fingerprint='verified',version='causal-level-book-v7-mle-1')
        controller._experimental_structure_snapshot=AsyncMock(return_value={'unified_levels':list(o.structural_resistance_levels)})
        controller._experimental_session_high=lambda ticker,at:10.5
        frame=ReplayDerivedFrame(as_of=NOW,bar=dict(open=10.3,high=10.4,low=10.2,close=10.39,volume=1000),
                                 indicator={},sequence=1,ticker=a.ticker,timeframe='1s')
        with (patch('src.trading_runtime.vwap_resistance_ladder.evaluate',side_effect=AssertionError('legacy executor')),
              patch('src.trading_runtime.historical_hod.evaluate',side_effect=AssertionError('legacy executor'))):
            await controller._observe_episode_candle(frame)
        market=controller._candle_detector_states[a.ticker]['structural_recovery']
        assert market['early_squeeze_context']['close']==10.39
        assert market['early_squeeze_context']['levels']
        assert 'activated_at' not in market['early_squeeze_context']
    asyncio.run(run())
