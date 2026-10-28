import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest,json,hashlib
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import torch
from research.vectorized_backtest.v56.prefetch_pipeline import read_results,NextSessionPrefetch,prepare_next

class PrefetchTests(unittest.TestCase):
 def test_verified_cache_and_corruption(self):
  with TemporaryDirectory(dir='D:/TradingML/runtimes') as tmp:
   p=Path(tmp)/'cache.json';results={'x':{'net_pnl':3}};raw=json.dumps(results,sort_keys=True,separators=(',',':'));record=dict(results=results,results_sha256=hashlib.sha256(raw.encode()).hexdigest());p.write_text(json.dumps(record));self.assertEqual(read_results(p)[1],record)
   record['results']['x']['net_pnl']=4;p.write_text(json.dumps(record))
   with self.assertRaises(ValueError):read_results(p)
 def test_retry_is_admitted_bounded_and_failures_propagate(self):
  p=NextSessionPrefetch(None,reserve_gib=1,admission_gib=1,snapshot=lambda:SimpleNamespace(available=3*2**30))
  f=Future();f.set_result(None);p.future=f;p.day='day';p.cancel=SimpleNamespace(set=lambda:None)
  with patch.object(p,'start') as start:
   for _ in range(6):p.retry_after=0;p.reconsider('day',None)
   self.assertEqual(start.call_count,3)
  f=Future();f.set_exception(ValueError('bad input'));p.future=f;p.retry_after=0
  with self.assertRaises(ValueError):p.reconsider('day',None)
 def test_transfer_staging_preserves_views_and_budget(self):
  item=SimpleNamespace(host_tensors={'a':torch.arange(12).reshape(3,4).t()},resident_features=SimpleNamespace(host={'b':torch.arange(6,dtype=torch.float64)}),close=lambda:None)
  original=torch.empty
  def allocate(*a,**kw):kw.pop('pin_memory',None);return original(*a,**kw)
  with patch('research.vectorized_backtest.v56.prefetch_pipeline.psutil.virtual_memory',return_value=SimpleNamespace(available=2**40)),patch('research.vectorized_backtest.v56.prefetch_pipeline.torch.empty',side_effect=allocate):
   prepare_next(item,'D:/TradingML/runtimes/nonexistent-cache-for-test.json',dict(prefetch_transfer=True))
  _,(thin,resident)=item.prefetched_transfer;self.assertTrue(torch.equal(thin['a'],item.host_tensors['a']));self.assertTrue(torch.equal(resident['b'],item.resident_features.host['b']))
