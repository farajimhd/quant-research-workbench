"""Bind sparse market, full causal feature history and certified execution rows.

Top-N is a decision universe, not an observation filter. Holdings and pending
orders can query the same sparse backing store after leaving that universe.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
import argparse,json
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime
from pathlib import Path
from time import time
import numpy as np
import polars as pl
from .runtime import require_runtime,file_hash
from .materialize import rank_indices,write_json,owned_run
from .availability import configure_reader
from .feature_bank import CertifiedBank,CATALOG
from .splits import load_basis
from .source import arte_source

VERSION='v6-sparse-compact-input-v1'
KEY_STRIDE=1<<32


def vector_validity(features):
    valid=np.isfinite(features)
    columns=np.array([i for i,f in enumerate(CATALOG) if f.mask is not None])
    masks=np.array([CATALOG[i].mask for i in columns])
    valid[:,columns]&=features[:,masks]==1
    return valid


def feature_rows(bank,previous,identity,start_us,end_us):
    """Same certified split/history semantics as CertifiedBank.listing, NumPy only."""
    from .splits import append_session_flags,rvol_view,history_view
    left,right=bank.manifest['offsets'][identity]
    clocks=np.asarray(bank.clocks[left:right])
    values=np.concatenate((bank.scalar[left:right],bank.levels[left:right].reshape(-1,110)),axis=1)
    values=append_session_flags(rvol_view(values,bank.split_basis[identity]['rvol_price_factor']),bank.split_basis[identity])
    if previous is not None and identity in previous.manifest['offsets']:
        a,b=previous.manifest['offsets'][identity];a=max(a,b-119)
        old=np.concatenate((previous.scalar[a:b],previous.levels[a:b].reshape(-1,110)),axis=1)
        old=append_session_flags(old,previous.split_basis[identity])
        old=history_view(rvol_view(old,previous.split_basis[identity]['rvol_price_factor']),bank.split_basis[identity]['history_price_factor'])
        if len(clocks) and b>a and previous.clocks[b-1]>=clocks[0]:raise ValueError('History reaches current/future observations')
        clocks=np.concatenate((previous.clocks[a:b],clocks));values=np.concatenate((old,values))
    begin=max(0,int(np.searchsorted(clocks,start_us))-119);end=int(np.searchsorted(clocks,end_us,side='right'))
    return clocks[begin:end],values[begin:end]


def sparse_market(bars,liquid):
    """Causal carries stay distinct from actual interval observations/capacity."""
    bars=bars.rename({'volume':'share_volume','execution_volume':'bar_execution_volume','execution_notional':'bar_execution_notional'})
    rows=bars.join(liquid,on=['clock','listing'],how='full',coalesce=True,validate='1:1').sort('listing','clock')
    rows=rows.with_columns(
        (pl.col('price_valid').fill_null(0)==1).alias('observed'),
        pl.when(pl.col('price_valid')==1).then(pl.col('close')).otherwise(None).forward_fill().over('listing').alias('mark'),
        pl.when(pl.col('extremes_valid')==1).then(pl.col('high')).otherwise(None).alias('high'),
        pl.when(pl.col('extremes_valid')==1).then(pl.col('low')).otherwise(None).alias('low'),
        pl.col('trade_count').fill_null(0),pl.col('volume').fill_null(0.),pl.col('notional').fill_null(0.),
        pl.when(pl.col('cumulative_volume')>0).then(pl.col('cumulative_notional')/pl.col('cumulative_volume')).otherwise(None).forward_fill().over('listing').alias('vwap'),
        pl.when(pl.col('quote_us')>0).then(pl.col('bid')).otherwise(None).forward_fill().over('listing').alias('bid'),
        pl.when(pl.col('quote_us')>0).then(pl.col('ask')).otherwise(None).forward_fill().over('listing').alias('ask'),
        pl.when(pl.col('quote_us')>0).then(pl.col('quote_us')).otherwise(None).forward_fill().over('listing').alias('quote_us'))
    rows=rows.with_columns(pl.when(pl.col('volume')>0).then(pl.col('notional')/pl.col('volume')).otherwise(None).alias('fill_price'))
    if rows.filter((pl.col('volume')<0)|(pl.col('notional')<0)|((pl.col('volume')>0)&(~pl.col('fill_price').is_finite()|(pl.col('fill_price')<=0)))).height:
        raise ValueError('Invalid executable volume/price')
    return rows.with_row_index('source_row')


def bind_session(item,args):
    day=item['day'];target=args.output/day;target.mkdir(exist_ok=True)
    sources=[root/day/'complete.json' for root in args.market_roots if (root/day/'complete.json').exists()]
    if len(sources)!=1:raise ValueError('Require exactly one completed market producer for '+day)
    receipt=sources[0];saved=json.loads(receipt.read_text());market_root=receipt.parent
    if saved['status']!='complete' or saved['identity']['session']!=item or saved['validation_opened']:
        raise ValueError('Market producer session contract mismatch')
    for name,checksum in saved['files'].items():
        if file_hash(market_root/name)!=checksum:raise ValueError('Market producer bytes changed: '+name)
    identity=dict(version=VERSION,session=item,sessions_sha256=file_hash(args.sessions),market_receipt_sha256=file_hash(receipt),
        implementation_sha256=file_hash(Path(__file__)),window=saved['identity']['window'],top_n=saved['identity']['top_n'])
    complete=target/'complete.json'
    if complete.exists():
        value=json.loads(complete.read_text())
        if value['identity']!=identity or any(file_hash(target/k)!=v for k,v in value['files'].items()):raise ValueError('Compact resume identity/hash mismatch')
        return value
    began=time();eligibility=json.loads((market_root/'eligibility.json').read_text());members=eligibility['listings']
    clocks=np.load(market_root/'clocks.npy',allow_pickle=False);bars=pl.read_parquet(market_root/'backing.parquet')
    # Every price-eligible listing already has a known prior closing trade.
    # Absence of a current price bar must not remove its rolling-volume rank.
    volume=np.zeros((len(clocks),len(members)),dtype=np.float64)
    volume[bars['clock'].to_numpy()-clocks[0],bars['listing'].to_numpy()]=bars['volume'].to_numpy()
    top,scores=rank_indices(volume,np.ones(volume.shape,dtype=bool),identity['window'],identity['top_n'])
    del volume
    np.save(target/'clocks.npy',clocks,allow_pickle=False);np.save(target/'top_indices.npy',top.astype(np.int32),allow_pickle=False)
    bank=CertifiedBank(item['feature_root'],expected_day=day)
    prior=CertifiedBank(item['previous_feature_root']) if item.get('previous_feature_root') else None
    mapping=json.loads(Path(item['identity_map']).read_text())
    if mapping['bank_certificate_sha256']!=bank.certificate_hash or set(mapping['listing_to_ticker'])!=set(bank.manifest['offsets']):raise ValueError('Feature identity certificate mismatch')
    bank.split_basis,split_hash=load_basis(item['split_certificate'],bank,prior,mapping['listing_to_ticker'])
    prior_split_hash=None
    if prior:
        certificate=json.loads(Path(item['previous_split_certificate']).read_text())
        prior_mapping={k:v['ticker'] for k,v in certificate['listings'].items()}
        if set(prior_mapping)!=set(prior.manifest['offsets']):raise ValueError('Incomplete prior identity context')
        prior.split_basis,prior_split_hash=load_basis(item['previous_split_certificate'],prior,None,prior_mapping,rvol_only=True)
    chunks=[];offsets=[0];features_bytes=0
    for listing,row in enumerate(members):
        if mapping['listing_to_ticker'].get(row['listing_id'])!=row['ticker']:raise ValueError('Eligible feature identity changed')
        stamps,values=feature_rows(bank,prior,row['listing_id'],int(clocks[0])*1000000,int(clocks[-1])*1000000)
        if np.any(stamps%1000000) or np.any(np.diff(stamps)<=0):raise ValueError('Feature clock is not a strict completed one-second sequence')
        valid=vector_validity(values);seconds=stamps//1000000
        if (seconds<0).any() or (seconds>=KEY_STRIDE).any():raise ValueError('Feature key clock outside declared envelope')
        features_bytes+=values.nbytes+valid.nbytes+seconds.nbytes
        if features_bytes>args.maximum_feature_gib*1024**3:raise MemoryError('Compact feature producer exceeds declared memory bound')
        chunks.append((listing*KEY_STRIDE+seconds,values,valid));offsets.append(offsets[-1]+len(seconds))
    count=offsets[-1]
    if not count:raise ValueError('No certified feature observations')
    feature_keys=np.lib.format.open_memmap(target/'feature_keys.npy',mode='w+',dtype=np.int64,shape=(count,))
    feature_values=np.lib.format.open_memmap(target/'features.npy',mode='w+',dtype=np.float32,shape=(count,len(CATALOG)))
    feature_valid=np.lib.format.open_memmap(target/'feature_valid.npy',mode='w+',dtype=np.bool_,shape=(count,len(CATALOG)))
    for i,(keys,values,valid) in enumerate(chunks):
        a,b=offsets[i:i+2];feature_keys[a:b]=keys;feature_values[a:b]=values;feature_valid[a:b]=valid
    feature_keys.flush();feature_values.flush();feature_valid.flush();del chunks
    if np.any(np.diff(feature_keys)<=0):raise ValueError('Feature identity key ordering failed')
    feature_table=pl.DataFrame(dict(feature_row=np.arange(count,dtype=np.int64),listing=np.asarray(feature_keys)//KEY_STRIDE,
        clock=np.asarray(feature_keys)%KEY_STRIDE)).sort('clock')
    configure_reader(Path(__file__).resolve().parents[4])
    source=arte_source.load_build(item['source_manifest'],item['source_ledger'],[day])
    reader=arte_source.reader(threads=1)
    from .broker_certificate import certify_broker_units
    from .prepare import liquidity_sql
    from zoneinfo import ZoneInfo
    origin=int(datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())*1000000
    frames=[]
    try:
        market=certify_broker_units(reader,source,item['source_ledger'],date.fromisoformat(day),tuple(row['ticker'] for row in members))
        for offset in range(0,len(members),64):
            names=[row['ticker'] for row in members[offset:offset+64]]
            statement=liquidity_sql(market,names,date.fromisoformat(day),origin,(int(clocks[0])-1)*1000000,int(clocks[-1])*1000000)
            frames.extend(pl.from_arrow(batch) for batch in reader.iter_arrow_record_batches(statement))
    finally:reader.close()
    if not frames:raise ValueError('No certified broker observations')
    ids={row['ticker']:i for i,row in enumerate(members)}
    liquid=pl.concat(frames).with_columns((pl.col('time_us')//1000000).alias('clock'),pl.col('ticker').replace_strict(ids).cast(pl.Int32).alias('listing')).drop('time_us','ticker')
    rows=sparse_market(bars,liquid).sort('clock').join_asof(feature_table,on='clock',by='listing',strategy='backward',check_sortedness=False).sort('listing','clock')
    observed=rows.filter(pl.col('observed'))
    if observed['feature_row'].null_count():raise ValueError('Observed market bar has no certified feature history')
    indices=observed['feature_row'].to_numpy();prices=np.exp(np.asarray(feature_values[indices,3],dtype=np.float64))
    if not np.all(np.asarray(feature_valid[indices,3])) or not np.array_equal(np.asarray(feature_keys[indices])%KEY_STRIDE,observed['clock'].to_numpy()) or not np.allclose(prices,observed['close'].to_numpy(),rtol=2e-6,atol=1e-4):
        raise ValueError('Feature/financial observed prices, identities or clocks disagree')
    rows=rows.drop('source_row').with_row_index('source_row')
    rows.write_parquet(target/'market.parquet')
    market_keys=rows['listing'].to_numpy().astype(np.int64)*KEY_STRIDE+rows['clock'].to_numpy()
    if np.any(np.diff(market_keys)<=0):raise ValueError('Market identity key ordering failed')
    np.save(target/'market_keys.npy',market_keys,allow_pickle=False)
    selection=pl.DataFrame(dict(clock=np.repeat(clocks,top.shape[1]),slot=np.tile(np.arange(top.shape[1]),len(clocks)),listing=top.ravel(),rolling_volume=scores.ravel())).filter(pl.col('listing')>=0)
    selection=selection.sort('clock').join_asof(rows.select('listing','clock','source_row','feature_row'),on='clock',by='listing',strategy='backward',check_sortedness=False).sort('clock','slot')
    selection.write_parquet(target/'top_blocks.parquet')
    files={p.name:file_hash(p) for p in target.iterdir() if p.is_file() and p.name!='complete.json'}
    result=dict(status='complete',identity=identity,files=files,ready_for_replay=True,validation_opened=False,
        listings=members,clocks=len(clocks),market_rows=rows.height,feature_rows=count,feature_offsets=offsets,
        bank_certificate_sha256=bank.certificate_hash,prior_certificate_sha256=prior.certificate_hash if prior else None,
        split_certificate_sha256=split_hash,prior_split_certificate_sha256=prior_split_hash,broker_certificate=market.evidence,
        data_eligibility_counts=eligibility['counts'],unsupported=eligibility.get('unsupported',[]),elapsed_seconds=time()-began,
        contract='sparse completed observations; full listing history; top-N plus retained identities queried causally at runtime')
    write_json(complete,result);return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--market-roots',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--maximum-feature-gib',type=float,default=2.)
    args=p.parse_args(argv);args.output=require_runtime(args.output)
    if not 1<=args.workers<=4 or args.maximum_feature_gib<=0:raise ValueError('Invalid bounded preparation budget')
    spec=json.loads(args.sessions.read_text());training=spec['training']
    if len(training)!=30 or len({s['day'] for s in training})!=30 or {s['day'] for s in training}&{s['day'] for s in spec['validation']}:raise ValueError('Require 30 disjoint training sessions')
    results=[];failures=[]
    with owned_run(args.output,version=VERSION),ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(bind_session,item,args):item['day'] for item in training}
        for future in as_completed(jobs):
            try:
                result=future.result();results.append(result);event=dict(day=jobs[future],status='complete',market_rows=result['market_rows'],feature_rows=result['feature_rows'],elapsed_seconds=result['elapsed_seconds'])
            except Exception as error:event=dict(day=jobs[future],status='failed',error=str(error));failures.append(event)
            print(json.dumps(event),flush=True)
            write_json(args.output/'status.json',dict(completed=len(results),failed=len(failures),total=30,failures=failures,validation_opened=False))
    write_json(args.output/'complete.json',dict(version=VERSION,status='failed' if failures else 'complete',
        sessions_sha256=file_hash(args.sessions),ready_for_replay=len(results)==30 and not failures,validation_opened=False,
        receipts=[dict(day=r['identity']['session']['day'],sha256=file_hash(args.output/r['identity']['session']['day']/'complete.json')) for r in results],failures=failures))
    return int(bool(failures))


if __name__=='__main__':raise SystemExit(main())
