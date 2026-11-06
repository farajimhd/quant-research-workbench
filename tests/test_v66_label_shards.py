"""Source parity, target preservation, boundary and sealed-sidecar checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import polars as pl
from research.market_labels.v1.build import decode,build_day,verify_day,sha
from research.market_shards.v1.contract import origin_us
from research.vectorized_backtest.v66.shard_math import scalar_features
from research.vectorized_backtest.v66.features import BASE


class Labels(unittest.TestCase):
    def fixture(self,root,*,constant=False):
        day='2026-01-05';origin=origin_us(day);n=180
        close=np.ones(n)*5 if constant else 5+.2*np.sin(np.arange(n)/9)
        frame=pl.DataFrame(dict(ticker=['TEST']*n,time_us=origin+(14401+np.arange(n))*1_000_000,
            open=close,high=close+.01,low=close-.01,close=close,volume=np.ones(n)*1000,
            trade_count=np.ones(n,dtype=np.int64)*10,notional=close*1000,price_valid=np.ones(n,dtype=np.int64),extremes_valid=np.ones(n,dtype=np.int64)))
        bars=root/'bars'/day;bars.mkdir(parents=True);frame.write_parquet(bars/'trade-1000.parquet')
        (bars/'complete.json').write_text(json.dumps(dict(files={'trade-1000.parquet':sha(bars/'trade-1000.parquet')})))
        scanner=root/'scanner'/day;scanner.mkdir(parents=True)
        frame.select('time_us','ticker').with_columns(pl.lit(1).alias('rank'),pl.lit(600).alias('trade_count_60s')).write_parquet(scanner/'rank.parquet')
        (scanner/'complete.json').write_text(json.dumps(dict(source_hash='test',files={'rank.parquet':sha(scanner/'rank.parquet')})))
        (root/'source.json').write_text(json.dumps(dict(hash='test')))
        (root/'training.json').write_text(json.dumps(dict(source_hash='test',sessions=[dict(day=day,selected=['TEST'])],shard_seals={day:sha(bars/'complete.json')})))
        return day,frame

    def test_exact_v66_macd_and_warmup(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as d:
            day,frame=self.fixture(Path(d));bars,excluded=decode(frame)
            clocks=origin_us(day)//1_000_000+np.arange(14401,34201)
            base,*_=scalar_features(frame,clocks,np.arange(frame.height),origin_us(day)//1_000_000)
            names={f.name:i for i,f in enumerate(BASE)}
            ix=((bars['time_us'].to_numpy()-frame['time_us'][0])//1_000_000).astype(int)
            for side in ('line','signal'):
                np.testing.assert_array_equal(bars['macd_'+side],base[ix,names['macd_'+side+'_rel']].astype(float)*bars['close'].to_numpy())
            self.assertEqual(excluded.height,33)

    def test_build_resume_suppression_and_corruption(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as d:
            root=Path(d);day,_=self.fixture(root)
            task=(str(root/'training.json'),day,'training',str(root/'labels'),{'test':1})
            proof=build_day(task);self.assertEqual(proof['source_rows'],180)
            self.assertEqual(proof['valid_rows']+proof['excluded_rows'],180)
            self.assertEqual(build_day(task),proof)
            folder=root/'labels'/day;record=proof['listings'][0]
            rows=pl.read_parquet(folder/record['file'])
            self.assertIn('action_1a',rows.columns)
            self.assertTrue(rows['scanner_member'].all())
            self.assertTrue(pl.read_parquet(folder/'pairs.parquet')['right_censored'][-1])
            with self.assertRaises(ValueError):verify_day(folder,{'test':2})
            with (folder/record['file']).open('ab') as out:out.write(b'corrupt')
            with self.assertRaises(ValueError):verify_day(folder)

    def test_no_profitable_pairs(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as d:
            root=Path(d);day,_=self.fixture(root,constant=True)
            proof=build_day((str(root/'training.json'),day,'training',str(root/'labels'),{}))
            self.assertEqual(proof['selected_pairs'],0)

    def test_app_reads_saved_1a_and_1b_and_rejects_stale_hash(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.backend.research_model_service import router
        from research.market_labels.v1 import audit
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as d:
            root=Path(d);day,_=self.fixture(root)
            output=root/'labels';proof=build_day((str(root/'training.json'),day,'training',str(output),{}))
            dataset=dict(binding={},days=[dict(day=day,certificate=sha(output/day/'complete.json'))])
            app=FastAPI();app.include_router(router)
            with patch.object(audit,'published',return_value=({'sha256':'pinned'},dataset,output)), TestClient(app) as client:
                for phase in ('1a','1b'):
                    response=client.get('/api/research/models/v66/labels/metadata',params=dict(day=day,listing_id='TEST',phase=phase))
                    self.assertEqual(response.status_code,200,response.text)
                    for view in ('combined','flat','held','reference'):
                        response=client.get('/api/research/models/v66/labels/chart',params=dict(day=day,listing_id='TEST',phase=phase,view=view,dataset_sha256='pinned'))
                        self.assertEqual(response.status_code,200,response.text)
                        data=response.json();self.assertEqual(len(data['labels']),len(data['candles']))
                        self.assertTrue(all(c['endTime']==r['time_us']/1e6 for c,r in zip(data['candles'],data['labels'])))
                response=client.get('/api/research/models/v66/labels/chart',params=dict(day=day,listing_id='TEST',dataset_sha256='stale'))
                self.assertEqual(response.status_code,409)
                response=client.get('/api/research/models/v66/labels/market',params=dict(day=day,selection='selected'))
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue(all(r['selected'] for r in response.json()['rows']))


if __name__=='__main__':unittest.main()
