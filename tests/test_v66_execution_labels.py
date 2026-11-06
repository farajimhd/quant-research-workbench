"""Standing quote persistence, withdrawal and state-safe economic supervision."""
import unittest
import polars as pl
from research.market_labels.v3.selection import execution_book, price_rows, copy_targets
from tests.test_v66_spread_labels import SpreadLabels


class ExecutionLabels(unittest.TestCase):
    def test_persistence_withdrawal_and_recovery(self):
        e=pl.DataFrame(dict(ticker=['A']*4,time_us=[1,4,6,8],quote_us=[.5,3.5,5.5,7.5],
            bid=[10.,10.,11.,10.],ask=[10.1]*4,bid_size=[100.,0.,100.,100.],ask_size=[100.]*4,book_valid=[1,1,0,1])).with_columns(
                (pl.col('time_us')*1_000_000).cast(pl.Int64),(pl.col('quote_us')*1_000_000).cast(pl.Int64))
        clocks=pl.DataFrame(dict(listing_id=['A']*10,time_us=[i*1_000_000 for i in range(10)]))
        b=execution_book(e,clocks)
        self.assertEqual(b['spread_quote_valid'].to_list(),[False,True,True,True,False,False,False,False,True,True])
        self.assertEqual(b['quote_age_seconds'][3],2.5)
        self.assertEqual(b['quote_book_reason'][5],'no_displayed_size')
        self.assertEqual(b['quote_book_reason'][7],'invalid_book')
        with self.assertRaises(ValueError):execution_book(pl.concat([e,e.head(1)]),clocks)
        with self.assertRaises(ValueError):execution_book(e.with_columns(pl.col('time_us').alias('quote_us')),clocks)

    def test_unknown_rejected_episode_is_not_wait(self):
        rows,quotes=SpreadLabels().fixture()
        from research.market_labels.v2.selection import quote_book
        book=quote_book(quotes).with_columns(pl.lit(0.1).alias('quote_age_seconds'),pl.lit('valid').alias('quote_book_reason'))
        book=book.filter(pl.col('time_us')!=2_000_000)
        priced=price_rows(rows,book,30)
        decisions=pl.DataFrame(dict(listing_id=['A'],pair_id=[1],selected=[False],group_id=[None],allocation_ratio=[0.],active_score_sum=[0.],active_count=[0],time_us=[1_000_000],entry_target_us=[8_000_000],ask=[10.04]))
        out=copy_targets(priced,decisions,30,5)
        self.assertEqual(out['action'][1],'UNLABELLED')
        self.assertEqual(out['teacher_probabilities'][1].to_list(),[0.]*4)
        self.assertFalse(out['economic_label_valid'][1])


if __name__=='__main__':unittest.main()
