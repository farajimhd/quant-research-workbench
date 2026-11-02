"""Independent dense-count oracle and consumer smoke for a bounded session."""
import argparse
import json
from pathlib import Path
import numpy as np
import polars as pl
import pyarrow as pa
from . import sql, source
from .contract import origin_us, write_json
from .data import Session


def audit(root,day,c):
    frozen=json.loads((root/'source.json').read_text())
    gate=pl.read_parquet(root/'scanner'/day/'eligibility.parquet').filter(pl.col('eligible')==1)
    tickers=sorted(gate['ticker'].to_list());mapping={t:i for i,t in enumerate(tickers)}
    # One bounded first-ten-minute reference read, using a different algorithm
    # from SQL's sparse score segments. Captures missing-second expiry and ties.
    statement=f"SELECT ticker,bucket_index,trade_count FROM arte.bars_v1 WHERE {sql.scope(frozen['source_build'],day,source.attempts(root,day,tickers))} AND resolution_ms=1000 AND bucket_index>=14400 AND bucket_index<15000 ORDER BY ticker,bucket_index FORMAT ArrowStream"
    batches=list(c.iter_arrow_record_batches(statement))
    frame=pl.from_arrow(pa.Table.from_batches(batches))
    values=np.zeros((600,len(tickers)),dtype=np.int64)
    rows=frame['bucket_index'].to_numpy().astype(np.int64)-14400
    columns=frame['ticker'].replace_strict(mapping).to_numpy()
    values[rows,columns]=frame['trade_count'].to_numpy()
    cumulative=values.cumsum(axis=0);score=cumulative.copy();score[60:]-=cumulative[:-60]
    chosen=np.argsort(-score,axis=1,kind='stable')[:,:5]
    output=pl.read_parquet(root/'scanner'/day/'rank.parquet')
    observed=output.filter(pl.col('time_us')<=origin_us(day)+15000_000000)
    r=np.repeat(np.arange(600),5);rank=np.tile(np.arange(1,6),600);col=chosen.reshape(-1)
    positive=score[r,col]>0
    expected=pl.DataFrame(dict(time_us=(origin_us(day)+(r+14401)*1000000)[positive],rank=rank[positive].astype(np.uint8),ticker=np.array(tickers)[col[positive]],trade_count_60s=score[r,col][positive].astype(np.uint64)))
    if not expected.equals(observed):raise ValueError('Independent dense scanner oracle mismatch')
    previous=frozen['predecessors'][day]
    close_tickers=tickers[:32]
    low,high=frozen['regular'][previous]
    prior=source.query(c,f"SELECT ticker,argMax(close_int,bucket_index)/10000. AS close FROM arte.bars_v1 WHERE {sql.scope(frozen['source_build'],previous,source.attempts(root,previous,close_tickers))} AND resolution_ms=1000 AND price_valid=1 AND bucket_index>={low} AND bucket_index<{high} GROUP BY ticker")
    known={r['ticker']:r['close'] for r in prior}
    for row in gate.filter(pl.col('ticker').is_in(close_tickers)).iter_rows(named=True):
        if row['ticker'] not in known or not np.isclose(row['close'],known[row['ticker']],rtol=1e-6,atol=1e-6):
            raise ValueError('Minute gate / original one-second regular close mismatch')
    role=next(r for r in ('training','validation','profile') if (root/(r+'.json')).exists() and any(x['day']==day for x in json.loads((root/(r+'.json')).read_text())['sessions']))
    session=Session(root/(role+'.json'),day)
    samples=output.gather_every(max(1,output.height//32)).head(32)
    checks={}
    for resolution in (1000,10000,60000,3600000,86400000):
        window=session.windows(samples['ticker'].to_numpy(),samples['time_us'].to_numpy(),resolution)
        if not np.isfinite(window['values']).all():raise ValueError('Nonfinite model batch')
        checks[str(resolution)]=dict(shape=list(window['values'].shape),bytes=window['values'].nbytes+window['valid'].nbytes,valid=int(window['valid'].sum()))
    for side in ('bid','ask'):
        window=session.windows(samples['ticker'].to_numpy(),samples['time_us'].to_numpy(),side=side)
        checks[side]=dict(shape=list(window['values'].shape),bytes=window['values'].nbytes+window['valid'].nbytes,valid=int(window['valid'].sum()))
    result=dict(day=day,oracle_clocks=600,oracle_rows=observed.height,oracle_exact=True,prior_close_checks=len(prior),windows=checks)
    write_json(root/f'audit-{day}.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--day',required=True)
    p.add_argument('--endpoint',default='http://127.0.0.1:8123');p.add_argument('--env-file',type=Path,default=Path('D:/TradingML/secrets/.env'))
    a=p.parse_args(argv);c=source.client(a.endpoint,a.env_file)
    try:print(json.dumps(audit(a.output,a.day,c),indent=2))
    finally:c.close()


if __name__=='__main__':main()
