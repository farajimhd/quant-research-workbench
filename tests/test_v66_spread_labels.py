"""Quote timing, per-entry economics and retained EXIT masks."""
import unittest
import polars as pl
from research.market_labels.v2.selection import quote_book,price_rows,select,copy_targets
from research.rl_trading.v6.market_teacher_preview import Config,group


class SpreadLabels(unittest.TestCase):
    def fixture(self):
        times=[1,2,3,4,8,9]
        asks=[10.04,10.,9.99,10.05,10.07,10.02]
        bids=[10.03,9.99,9.98,10.04,10.06,10.01]
        quotes=pl.DataFrame([dict(ticker='A',side=side,time_us=t*1_000_000,close=values[i],close_size=100.,
            event_count=1,last_event_us=t*1_000_000-100) for i,t in enumerate(times) for side,values in [('bid',bids),('ask',asks)]])
        rows=pl.DataFrame(dict(listing_id=['A']*6,pair_id=[1]*6,time_us=[t*1_000_000 for t in times],
            close=[10.]*6,action_1a=['ENTRY']*4+['EXIT']*2,entry_target_us=[8_000_000]*4+[None,None],
            entry_quality=[1.]*6,exit_quality=[1.]*6,exit_gain=[None]*4+[.06,.01]))
        return rows,quotes

    def test_spread_fees_and_earliest_qualifying_entry(self):
        rows,quotes=self.fixture();priced=price_rows(rows,quote_book(quotes),30)
        self.assertEqual(priced['spread_entry_eligible'].to_list(),[False,True,True,False,False,False])
        row=priced.row(1,named=True)
        expected=((10.06-10.)*.5**(6/30)-.01)/10.005
        self.assertAlmostEqual(row['spread_score'],expected,places=12)
        self.assertGreater(priced['spread_score'][2],row['spread_score'])
        decisions=select(priced,pl.DataFrame(dict(listing_id=['A'],pair_id=[1],liquidity_accepted=[True])))
        self.assertEqual(decisions['time_us'][0],2_000_000)
        members,_=group(decisions.filter(pl.col('selected')),Config())
        fields=['listing_id','pair_id','group_id','allocation_ratio','active_score_sum','active_count']
        decisions=decisions.join(members.select(fields),on=['listing_id','pair_id'])
        copied=copy_targets(priced,decisions,30,5)
        # An unqualified alternative ENTRY inside the canonical held interval
        # remains HOLD; it must never become another ENTRY signal.
        self.assertEqual(copied['action'].to_list(),['WAIT','ENTRY','ENTRY','HOLD','EXIT','WAIT'])
        self.assertFalse(copied['spread_entry_eligible'][3])
        self.assertEqual(copied['action_1a'].to_list(),rows['action_1a'].to_list())
        self.assertFalse(copied['spread_exit_eligible'][-1])

    def test_missing_crossed_future_and_empty_quote_sides_are_invalid(self):
        _,quotes=self.fixture()
        for bad in [quotes.filter(~((pl.col('time_us')==1_000_000)&(pl.col('side')=='ask'))),
            quotes.with_columns(pl.when(pl.col('side')=='bid').then(20.).otherwise(pl.col('close')).alias('close')),
            quotes.with_columns(pl.col('time_us').alias('last_event_us')),
            quotes.with_columns(pl.lit(0.).alias('close_size'))]:
            self.assertFalse(quote_book(bad)['spread_quote_valid'][0])
        with self.assertRaises(ValueError):quote_book(pl.concat([quotes,quotes.head(1)]))

    def test_original_target_is_required_and_missing_quotes_are_explicit(self):
        rows,quotes=self.fixture()
        # The positive price witness must belong to the saved original EXIT cluster.
        rows=rows.with_columns(pl.when(pl.col('time_us')==8_000_000).then(pl.lit('HOLD')).otherwise(pl.col('action_1a')).alias('action_1a'))
        result=price_rows(rows,quote_book(quotes),30)
        self.assertFalse(result['spread_entry_eligible'].any())
        self.assertEqual(result['spread_entry_reason'][1],'target_not_1a_exit')
        result=price_rows(rows,quote_book(quotes.filter(pl.col('side')=='bid')),30)
        self.assertEqual(result['spread_entry_reason'][1],'entry_quote_unavailable')


if __name__=='__main__':unittest.main()
