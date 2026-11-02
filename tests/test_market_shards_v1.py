"""Causality, masks, source integrity and bounded publication contracts."""
import json
import tempfile
import unittest
from unittest.mock import patch
import pyarrow as pa
from pathlib import Path
import numpy as np
import polars as pl
from rich.console import Console
from research.market_shards.v1 import contract, sql, source, data, transport, progress


class MarketShardTests(unittest.TestCase):
    def test_empty_certified_stream_and_sealed_resume(self):
        base=Path('D:/TradingML/runtimes/market_shards/implementation/tests');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            path=Path(temporary)/'empty.parquet'
            batch=pa.record_batch([pa.array([],type=pa.string()),pa.array([],type=pa.int64())],names=['ticker','time_us'])
            with patch.object(transport,'batches',return_value=iter([batch])):
                saved=transport.stream(None,'SELECT fixture',path,['ticker','time_us'])
            self.assertEqual(saved['rows'],0)
            self.assertTrue(transport.stream(None,'SELECT fixture',path,['ticker','time_us'])['reused'])
            with self.assertRaises(ValueError):transport.stream(None,'SELECT changed',path,['ticker','time_us'])

    def test_partial_stream_never_published(self):
        base=Path('D:/TradingML/runtimes/market_shards/implementation/tests');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            path=Path(temporary)/'partial.parquet'
            def broken():
                yield pa.record_batch([['A'],[1]],names=['ticker','time_us'])
                raise ConnectionError('fixture disconnect')
            with patch.object(transport,'batches',return_value=broken()):
                with self.assertRaises(ConnectionError):transport.stream(None,'SELECT fixture',path,['ticker','time_us'])
            self.assertFalse(path.exists());self.assertFalse(path.with_suffix('.json').exists())

    def test_contract_excludes_fundamentals_and_v7(self):
        self.assertEqual(len(contract.CONTRACT['excluded']),5)
        self.assertEqual(sum(contract.TRADE_WINDOWS.values()),156)
        self.assertEqual(contract.QUOTE_WINDOWS,{1000:60})

    def test_split_chronological_no_overlap(self):
        split=contract.split_days([f'2026-01-{d:02}' for d in range(2,12)][::-1])
        self.assertEqual(len(split['training']),8)
        self.assertLess(max(split['training']),min(split['validation']))
        with self.assertRaises(ValueError):contract.split_days(['2026-01-02']*2)

    def test_dst_origin(self):
        self.assertEqual(contract.origin_us('2026-03-09')-contract.origin_us('2026-03-06'),71*3600_000000)

    def test_opening_known_splits_only(self):
        rows=[dict(provider_ticker='A',execution_date='2026-01-05',split_from=10,split_to=1,inserted_at='2026-01-04 00:00:00')]
        self.assertEqual(source.price_factor(rows,'A','2026-01-02','2026-01-05'),10)
        self.assertEqual(source.price_factor(rows,'B','2026-01-02','2026-01-05'),1)
        rows[0]['inserted_at']='2026-01-05 14:00:00'
        self.assertEqual(source.price_factor(rows,'A','2026-01-02','2026-01-05'),1)

    def test_conflicting_splits_fail(self):
        base=dict(provider_ticker='A',execution_date='2026-01-05',split_from=10,split_to=1,inserted_at='2026-01-04 00:00:00')
        with self.assertRaises(ValueError):source.price_factor([base,dict(base,split_from=5)],'A','2026-01-02','2026-01-05')

    def test_completed_second_and_session_boundary(self):
        o=contract.origin_us('2026-01-05')
        targets,valid=data.scheduled_clocks('2026-01-05',[o+14401_000000],1000,60,[])
        self.assertEqual(valid.sum(),1)
        self.assertEqual(targets[0,-1],o+14401_000000)

    def test_incomplete_hour_excluded_and_overnight_scheduled(self):
        o=contract.origin_us('2026-01-05')
        targets,valid=data.scheduled_clocks('2026-01-05',[o+34200_000000],3600000,16,['2026-01-02'])
        self.assertEqual(targets[0,-1],o+32400_000000)
        self.assertTrue(valid.all())
        self.assertEqual(len(targets[0]),16)

    def test_market_key_and_cross_batch_validation(self):
        frame=pl.DataFrame({'ticker':['A','A'],'time_us':[1,2],'close':[1.,2.]})
        last=transport.validate(frame,['ticker','time_us'])
        self.assertEqual(last,('A',2))
        with self.assertRaises(ValueError):transport.validate(frame,['ticker','time_us'],last)
        with self.assertRaises(ValueError):transport.validate(frame.reverse(),['ticker','time_us'])

    def test_nonfinite_rejected(self):
        frame=pl.DataFrame({'ticker':['A'],'time_us':[1],'close':[float('nan')]})
        with self.assertRaises(ValueError):transport.validate(frame,['ticker','time_us'])

    def test_future_execution_quote_rejected(self):
        f=pl.DataFrame({'ticker':['A'],'time_us':[1],'quote_us':[2],'capacity':[1.]})
        with self.assertRaises(ValueError):transport.validate(f,['ticker','time_us'])

    def test_no_unbounded_or_raw_source_sql(self):
        q=sql.ranking('build','2026-01-05',{'A':'00000000-0000-0000-0000-000000000001'})
        self.assertIn('groupArraySorted(5)',q)
        self.assertIn('[0,60]',q)
        self.assertNotIn('market_sip_compact',q)
        with self.assertRaises(ValueError):sql.scope('b','2026-01-05',{})

    def test_render_narrow_wide_stale_and_failed(self):
        import io,time
        state=dict(status='running',stage='extract',heartbeat=time.time()-100,completed=2,reused=1,
            failed=1,blocked=1,queued=3,total=8,active=1,elapsed=100,
            phase_eta_seconds=None,workers={'0':dict(day='2026-01-05',state='active',stage='execution-36000 12/22',rows=10000,bytes=200000,started=time.time()-10)},errors=['2026-01-06: missing coverage'])
        for width in (80,140):
            out=io.StringIO();c=Console(file=out,width=width,color_system=None)
            c.print(progress.render(state,width));text=out.getvalue()
            self.assertIn('STALE',text);self.assertIn('missing coverage',text)

    def test_loader_masks_sparse_seconds_and_rejects_tail(self):
        # Exercise the real window gather against a fake immutable storage read.
        s=object.__new__(data.Session);s.day='2026-01-05';s.item=dict(selected=['A'],hourly=[s.day],daily=[]);s.source={'splits':[]}
        o=contract.origin_us(s.day);t=o+14403_000000
        frame=pl.DataFrame(dict(ticker=['A','A'],time_us=[t-2000000,t],open=[1.,3.],high=[1.,3.],low=[1.,3.],close=[1.,3.],volume=[1.,1.],trade_count=[1,1],notional=[1.,3.],price_valid=[1,1],extremes_valid=[1,1]))
        s._read=lambda *args:frame
        out=s.windows(['A'],[t]);self.assertEqual(out['valid'][0,:,0].sum(),2)
        self.assertFalse(out['valid'][0,-2].any());self.assertEqual(out['values'][0,-1,3],3)
        with self.assertRaises(ValueError):s.windows(['A'],[o+34201_000000])

    def test_daily_missing_session_not_compressed(self):
        s=object.__new__(data.Session);s.day='2026-01-06';s.item=dict(selected=['A'],daily=['2026-01-02','2026-01-05']);s.source={'splits':[]}
        row=pl.DataFrame(dict(ticker=['A'],time_us=[contract.origin_us('2026-01-02')+57600_000000],available_at_us=[contract.origin_us('2026-01-02')+57600_000000],open=[1.],high=[1.],low=[1.],close=[1.],volume=[1.],trade_count=[1],notional=[1.],price_valid=[1],extremes_valid=[1]))
        s._read=lambda day,*args:row if day=='2026-01-02' else None
        out=s.windows(['A'],[contract.origin_us(s.day)+14401_000000],86400000)
        self.assertTrue(out['valid'][0,-2].all());self.assertFalse(out['valid'][0,-1].any())


if __name__=='__main__':unittest.main()
