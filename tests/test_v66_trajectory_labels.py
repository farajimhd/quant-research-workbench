"""Reference state transitions, sparse clocks, endpoint integrity and UI projection."""
import unittest
import polars as pl
from research.market_labels.v4.trajectory import label


class TrajectoryLabels(unittest.TestCase):
    def fixture(self):
        rows=pl.DataFrame(dict(listing_id=['A']*7,time_us=[i*1_000_000 for i in [1,2,3,7,8,10,11]],
            action=['ENTRY']*7,teacher_probabilities=[[.8,.2,0.,0.]]*7))
        positions=pl.DataFrame(dict(listing_id=['A','A'],pair_id=[1,2],time_us=[2_000_000,10_000_000],
            entry_target_us=[8_000_000,11_000_000],selected=[True,True]))
        return rows,positions

    def test_single_endpoints_and_pre_action_state(self):
        rows,p=self.fixture();r=label(rows,p)
        self.assertEqual(r['action_1c'].to_list(),['WAIT','ENTRY','HOLD','HOLD','EXIT','ENTRY','EXIT'])
        self.assertEqual(r['position_state_before'].to_list(),[0,0,1,1,1,0,1])
        self.assertEqual(r['position_state_after'].to_list(),[0,1,1,1,0,1,0])
        self.assertEqual(r['position_age_candles'].to_list(),[0,0,1,5,6,0,1])
        self.assertEqual(r['remaining_duration_candles'].to_list(),[None,6,5,1,0,1,0])
        self.assertTrue(r.select(rows.columns).equals(rows))
        self.assertEqual(r['teacher_probabilities_1c'][1].to_list(),[1.,0.,0.,0.])

    def test_no_positions_and_flat_after_exit(self):
        rows,p=self.fixture();r=label(rows,p.head(0))
        self.assertEqual(set(r['action_1c']),{'WAIT'})
        self.assertFalse(r['remaining_duration_valid'].any())
        r=label(rows,p.head(1));self.assertEqual(r['position_state_before'][-1],0)
        self.assertIsNone(r['position_id_1c'][-1]);self.assertEqual(r['position_age_seconds'][-1],0)

    def test_missing_endpoints_and_conflicting_positions_fail(self):
        rows,p=self.fixture()
        with self.assertRaisesRegex(ValueError,'endpoint candle'):label(rows.filter(pl.col('time_us')!=8_000_000),p)
        with self.assertRaisesRegex(ValueError,'shared-boundary'):label(rows,p.with_columns(pl.when(pl.col('pair_id')==2).then(8_000_000).otherwise(pl.col('time_us')).alias('time_us')))
        with self.assertRaisesRegex(ValueError,'Duplicate candle'):label(pl.concat([rows,rows.head(1)]),p)

    def test_listing_isolation_and_input_order(self):
        rows,p=self.fixture();rows=pl.concat([rows,rows.with_columns(pl.lit('B').alias('listing_id'))]).reverse()
        r=label(rows,p);self.assertTrue(r.select(rows.columns).equals(rows))
        self.assertEqual(set(r.filter(pl.col('listing_id')=='B')['action_1c']),{'WAIT'})


if __name__=='__main__':unittest.main()
