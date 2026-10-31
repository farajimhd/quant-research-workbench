"""Prepare immutable, paged terminal liquidity from the original ARTE build.

No raw events, external acquisition, strategy features, or financial inheritance.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,time
from zoneinfo import ZoneInfo
from io import BytesIO
import polars as pl
from research.mlops.env import discover_env_files,load_env_files
from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password
from research.vectorized_backtest.v60.publication import write_json

VERSION='v59-certified-quote-size-execution-v2'  # Unchanged certified input-page schema.


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()


def literal(value):return "'"+str(value).replace('\\','\\\\').replace("'","\\'")+"'"


def sql(build,day,attempts,origin,begin,end):
    pairs=','.join(f'({literal(t)},toUUID({literal(a)}))' for t,a in sorted(attempts.items()))
    close=f'{origin}+(toInt64(bucket_index)+1)*100000'
    valid=f'quote_timestamp_us>0 AND quote_timestamp_us<=last_event_us AND last_event_us<{close}'
    return f'''SELECT ticker,intDiv(toInt64(bucket_index),10)*1000000+1000000+{origin} AS time_us,
      sum(execution_volume) AS capacity,
      argMaxIf(close_int,bucket_index,price_valid=1)/10000. AS mark,
      argMaxIf(bid_int,bucket_index,{valid})/10000. AS bid,
      argMaxIf(ask_int,bucket_index,{valid})/10000. AS ask,
      argMaxIf(bid_size,bucket_index,{valid}) AS bid_size,
      argMaxIf(ask_size,bucket_index,{valid}) AS ask_size,
      argMaxIf(quote_valid,bucket_index,{valid}) AS book_valid,
      argMaxIf(quote_timestamp_us,bucket_index,{valid}) AS quote_us
      FROM arte.liquidity_100ms_v1 WHERE build_id={literal(build)} AND session_date=toDate({literal(day)})
      AND (ticker,attempt_id) IN ({pairs}) AND bucket_index>={(begin-origin)//100000}
      AND bucket_index<{(end-origin)//100000} GROUP BY ticker,time_us ORDER BY time_us,ticker FORMAT ArrowStream'''


def prepare_day(client,job,role,day,output,source_runtime=None):
    assets=job['assets'][role][day]
    inputs=Path(next(a['path'] for a in assets if Path(a['path']).name=='clocks.npy')).parent
    history=Path(next(a['path'] for a in assets if Path(a['path']).name=='source_rows.npy')).parent
    receipt=json.loads((inputs/'complete.json').read_bytes());session=receipt['identity']['session']
    cert=json.loads((history/'complete.json').read_bytes());ids=cert['listing_ids']
    tickers=[receipt['listings'][i]['ticker'] for i in ids]
    if len(set(tickers))!=len(tickers):raise ValueError('Ambiguous listing/ticker execution identity')
    def source_file(value):
        path=Path(value)
        return Path(source_runtime)/path.relative_to(Path('D:/TradingML/runtimes')) if source_runtime else path
    source_path=source_file(session['source_manifest']);source=json.loads(source_path.read_bytes())
    if source['status']!='core_complete':raise ValueError('Incomplete source build')
    build=source['build_id'];ledger=source_file(session['source_ledger'])
    with sqlite3.connect('file:'+str(ledger)+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT ticker,attempt_id,status,output_rows,output_hash FROM units WHERE build_id=? AND session_date=? AND stage=?',(build,day,'broker_100ms')).fetchall()
    units={r[0]:r[1:] for r in rows}
    if any(t not in units or units[t][1]!='complete' for t in tickers):raise ValueError('Missing certified broker unit')
    attempts={t:units[t][0] for t in tickers}
    ny=ZoneInfo('America/New_York');origin=int(datetime.combine(datetime.fromisoformat(day).date(),time(),ny).timestamp()*1e6)
    cutoff=int(datetime.fromisoformat(session['end']).timestamp()*1e6)
    start=int(datetime.fromisoformat(session['start']).timestamp()*1e6)
    end=int(datetime.combine(datetime.fromisoformat(day).date(),time(20),ny).timestamp()*1e6)
    folder=output/role/day;folder.mkdir(parents=True,exist_ok=True)
    identity=dict(version=VERSION,build_id=build,input_receipt_sha256=sha(inputs/'complete.json'),history_receipt_sha256=sha(history/'complete.json'),
        source_manifest_sha256=sha(source_path),attempts=attempts,listing_ids=ids,tickers=tickers,start_us=start,cutoff_us=cutoff,end_us=end,validation_opened=role=='validation',capacity='displayed quote size consumed once per quote timestamp and side',quote_age_limit=None)
    complete=folder/'complete.json'
    if complete.exists():
        prior=json.loads(complete.read_bytes())
        if prior['identity']!=identity or any(sha(folder/n)!=h for n,h in prior['files'].items()):raise ValueError('Execution sidecar changed')
        return prior
    files={};pages=[]
    # Hour pages bound both the query result and subsequent host/GPU staging.
    spans=[(phase,begin,min(bound,begin+3600_000_000)) for phase,lower,bound in (('decision',start,cutoff),('tail',cutoff,end)) for begin in range(lower,bound,3600_000_000)]
    for phase,begin,stop in spans:
        name=f'{begin}.parquet';path=folder/name;seal=folder/(name+'.json')
        query=sql(build,day,attempts,origin,begin,stop);query_hash=hashlib.sha256(query.encode()).hexdigest()
        if seal.exists():
            proof=json.loads(seal.read_bytes())
            if proof['query_sha256']!=query_hash or proof['sha256']!=sha(path):raise ValueError('Execution page resume mismatch')
        else:
            batches=list(client.iter_arrow_record_batches(query))
            import pyarrow as pa
            frame=pl.from_arrow(pa.Table.from_batches(batches)) if batches else pl.DataFrame(schema=dict(ticker=pl.String,time_us=pl.Int64,capacity=pl.Float64,mark=pl.Float64,bid=pl.Float64,ask=pl.Float64,bid_size=pl.Float64,ask_size=pl.Float64,book_valid=pl.UInt8,quote_us=pl.Int64))
            if frame.height and (frame.select(pl.struct('ticker','time_us').is_duplicated().any()).item() or
                frame.filter((pl.col('time_us')<=begin)|(pl.col('time_us')>stop)|(pl.col('capacity')<0)|~pl.col('capacity').is_finite()).height):
                raise ValueError('Invalid execution page clock/capacity')
            temp=path.with_suffix('.partial');frame.write_parquet(temp);temp.replace(path)
            proof=dict(query_sha256=query_hash,sha256=sha(path),rows=frame.height,begin_us=begin,end_us=stop)
            write_json(seal,proof)
        files[name]=proof['sha256'];pages.append(dict(file=name,phase=phase,**proof))
    record=dict(identity=identity,files=files,pages=pages,status='complete')
    write_json(complete,record);return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--days',nargs='*')
    parser.add_argument('--source-runtime',type=Path,help='Explicit mapped location of the same pinned source runtime files')
    args=parser.parse_args()
    from research.vectorized_backtest.v60.runtime import require_runtime
    output=require_runtime(args.output);job=json.loads(args.job.read_bytes())
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),persistent=True,timeout_seconds=180,
        default_query_params=dict(readonly=1,max_threads=2,max_execution_time=150,max_memory_usage=2147483648))
    try:
        rows=[json.loads(x) for x in client.execute("SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name='liquidity_100ms_v1' FORMAT JSONEachRow").splitlines()]
        parts=[json.loads(x) for x in client.execute("SELECT DISTINCT disk_name FROM system.parts WHERE database='arte' AND table='liquidity_100ms_v1' AND active FORMAT JSONEachRow").splitlines()]
        if len(rows)!=1 or rows[0]['storage_policy']!='live_market_ssd' or any(p['disk_name']!='live_market_ssd' for p in parts):raise ValueError('Execution source storage authority mismatch')
        for role in ('training','validation'):
            for day in job[role+'_days']:
                if args.days and day not in args.days:continue
                value=prepare_day(client,job,role,day,output,args.source_runtime);print(json.dumps(dict(role=role,day=day,pages=len(value['pages']),rows=sum(p['rows'] for p in value['pages']))),flush=True)
    finally:client.close()


if __name__=='__main__':main()
