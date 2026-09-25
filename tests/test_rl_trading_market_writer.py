import polars as pl

from research.rl_trading.v1.build_phase2 import publish_market_values
from research.rl_trading.v1.common import digest, file_hash
from src.market_engine.level_book_store import write


def test_bounded_market_sort_matches_full_ordered_values(tmp_path):
    listings = [dict(ticker='A',listing_id='a'),dict(ticker='B',listing_id='b')]
    plan = dict(plan_hash='test',selected=listings,
        discount_policy=dict(macd_resolution_seconds=1.))
    sources = []
    for index,listing in enumerate(listings):
        times = list(range(57601))
        rows = []
        for side in ('long','short'):
            rows.extend(dict(time_us=time_us,ticker=listing['ticker'],
                listing_id=listing['listing_id'],side=side,
                can_open=side == 'long' and time_us % 100 == 0,
                value_available=True,open_value_available=True,
                entry_price=10.,capital_per_share=10.,
                open_profit_per_share=1.,open_value_per_share=1.,
                open_value_per_dollar=.1,status='ready',volume_60s=float(index+1))
                for time_us in times)
        frame = pl.DataFrame(rows).with_columns(
            pl.lit(index,dtype=pl.UInt32).alias('listing_index'),
            pl.lit(1.).alias('macd_resolution_seconds'))
        sources.append(frame)
        folder = tmp_path/'listings'/digest(listing)[:20]
        folder.mkdir(parents=True)
        frame.drop('listing_index','macd_resolution_seconds').write_parquet(folder/'coefficients.parquet')
        write(folder/'ready.json',dict(plan_hash='test',rows=115202,
            files={'coefficients.parquet':file_hash(folder/'coefficients.parquet')}))
    tensor = publish_market_values(tmp_path,plan)
    expected = pl.concat(sources).sort('time_us','listing_index','side')
    opening = ('can_open','value_available','open_value_available','entry_price',
        'capital_per_share','open_profit_per_share','open_value_per_share',
        'open_value_per_dollar')
    keys = ('time_us','listing_index','side')
    assert tensor['sort_engine'] == 'duckdb_external'
    assert pl.read_parquet(tmp_path/'market_hold_values.parquet').equals(
        expected.drop(*opening))
    assert pl.read_parquet(tmp_path/'market_open_values.parquet').equals(
        expected.filter(pl.col('can_open')).select(*keys,*opening))
