import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date, datetime, time, timedelta, UTC
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.backend.historical_liquidity_checkpoint import checkpoint, restore
from src.backend.replay_run_service import (
    ReplayRunController, ReplayRunDefinition, ReplayDerivedFrame,
)
from src.trading_runtime.journal import TradingJournal
from tests.test_replay_run_service import approved_configuration


class LiquidityCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_rejects_missing_liquidity_before_loading_or_starting_run(self):
        from src.backend.replay_run_service import ReplayRunService, RESTART_CHECKPOINT_SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run_id=str(uuid4());run_dir=root/run_id;run_dir.mkdir()
            (run_dir/'manifest.json').write_text(json.dumps({'run':{'status':'stopped'}}))
            (run_dir/'journal.sqlite3').touch()
            state=dict(schema_version=RESTART_CHECKPOINT_SCHEMA_VERSION,complete=True,
                       controller={},runtime={},identity={})
            with patch('src.backend.replay_run_service.TradingJournal') as journal, \
                    patch('src.backend.replay_run_service._checkpoint_has_strategy_observations',return_value=True), \
                    patch('src.backend.replay_run_service._definition_from_manifest',side_effect=AssertionError('must reject before loading execution')):
                journal.return_value.load_checkpoint.return_value={'state':state}
                with self.assertRaisesRegex(ValueError,'lacks historical liquidity'):
                    await ReplayRunService(runtime_root=root).resume(run_id)

    async def test_controller_restart_preserves_accumulator_and_following_gate_facts(self):
        definition = ReplayRunDefinition(session_date=date(2026, 8, 21),
            start_time=time(4), tickers=('ADXN',),
            configuration_revision=approved_configuration())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = ReplayRunController(definition, runtime_root=root)
            source._journal = TradingJournal(root / 'source.sqlite3')
            await source._initialize_runtime(record_configuration=False, record_lifecycle=False)
            stamp = datetime(2026, 8, 21, 8, 37, 5, tzinfo=UTC)
            source.current_time = stamp
            source._frame_cursor = {'as_of': stamp.isoformat()}
            source._historical_market_quality = {'ADXN': {
                'dollar_volume': 2588745.5838999934, 'share_volume': 295554.,
                'trade_buckets': [(stamp-timedelta(seconds=59, microseconds=1), 10),
                                  (stamp-timedelta(seconds=1, microseconds=7), 640)],
                'trade_bucket_head': 0, 'previous_bar_volume': 1865.,
                'spread_bps': 93.73668512995266,
            }}
            state = json.loads(json.dumps(source._restart_checkpoint_state(), default=str))
            resumed = ReplayRunController(definition, runtime_root=root,
                run_id=source.run_id, resume_state=state)
            resumed._journal = TradingJournal(root / 'resumed.sqlite3')
            try:
                await resumed._initialize_runtime(record_configuration=False, record_lifecycle=False)
                self.assertEqual(resumed._historical_market_quality, source._historical_market_quality)
                frame = ReplayDerivedFrame(ticker='ADXN', timeframe='1s',
                    as_of=stamp+timedelta(seconds=2), sequence=1,
                    bar={'volume':178., 'dollar_volume':2071.22, 'trade_count':9}, indicator={})
                before, after = {}, {}
                source._project_historical_market_quality(frame, before)
                resumed._project_historical_market_quality(frame, after)
                self.assertEqual(before, after)
                self.assertAlmostEqual(after['market.session_dollar_volume']['value'],2590816.8038999934)
                self.assertAlmostEqual(after['market.trade_rate_60s']['value'],649/60)
                old = deepcopy(state)
                del old['controller']['historical_liquidity']
                resumed._resume_state = old
                with self.assertRaisesRegex(ValueError, 'lacks historical liquidity'):
                    resumed._restore_restart_checkpoint()
                # Old immutable results remain reviewable; execution is rejected.
                resumed._restore_restart_checkpoint(review_only=True)
                resumed._checkpoint_projection_cache = None
                self.assertFalse(resumed._checkpoint_projection({'state':old})['resume_supported'])
            finally:
                source._journal.close()
                resumed._journal.close()

    def test_raw_mode_preserves_exact_clocks_head_and_volume_buckets(self):
        stamp = datetime(2026,8,21,8,1,2,123456,tzinfo=UTC)
        states={'X':dict(dollar_volume=12.25,share_volume=5.,raw_authority=True,
            trade_buckets=[(stamp-timedelta(seconds=61),1),(stamp,2)],
            trade_bucket_head=1,volume_buckets=[(1787299261,2.),(1787299262,3.)],spread_bps=10.)}
        result=restore(json.loads(json.dumps(checkpoint(states))))
        self.assertEqual(result,states)
        result['X']['dollar_volume']=0
        self.assertEqual(states['X']['dollar_volume'],12.25)

    def test_corrupt_history_fails_closed(self):
        base=checkpoint({'X':dict(dollar_volume=1.,share_volume=1.,trade_buckets=[])})
        for key,value in [('dollar_volume',float('nan')),('share_volume',-1),
                          ('trade_bucket_head',1),('raw_authority',1),('unexpected',0)]:
            bad=deepcopy(base);bad['tickers']['X'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):restore(bad)
