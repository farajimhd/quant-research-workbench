import unittest
import numpy as np
from .dataset import SAMPLE,check,novel


class DatasetContract(unittest.TestCase):
    def example(self):
        row=np.zeros((),dtype=SAMPLE);row['origin_us']=100000
        row['context']['end_us']=np.arange(64)+99937
        row['future']['end_us']=np.arange(64)+100001
        row['context']['values']=1;row['context']['valid']=1
        row['future']['values']=1;row['future']['valid']=1
        return row
    def test_future_and_purge(self):
        row=self.example();check(row,99000,101000)
        for field in ('context','future'):
            bad=row.copy()
            if field=='context':bad[field]['end_us'][0,-1]=100001
            else:bad[field]['end_us'][0]=100000
            with self.assertRaises(ValueError):check(bad)
        with self.assertRaises(ValueError):check(row,99999,None)
        with self.assertRaises(ValueError):check(row,None,100050)
    def test_source_valid_zero_not_removed(self):
        row=self.example();row['future']['values'][0,4]=0;check(row)
        row['future']['values'][0,4]=np.nan
        with self.assertRaises(ValueError):check(row)
    def test_ticker_partition_is_stable(self):
        self.assertEqual([novel(x) for x in ('A','B','C')],[novel(x) for x in ('A','B','C')])


if __name__=='__main__':unittest.main()
