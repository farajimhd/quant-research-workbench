"""Observer-only actual-fill details, read in the isolated observer worker."""
from pathlib import Path
import hashlib
import polars as pl
from .progress_sessions import read_verified


def attach(rows,receipt,index,remote_path):
    refs=receipt['metrics'].get('execution_orders')
    if not refs:return
    ref=refs[index];path=remote_path(Path(ref['receipt']))
    journal=read_verified(path);local=ref['candidate_index']
    if journal['candidate_sha256'][local]!=ref['sha256'] or journal['counts'][local]!=ref['orders']:
        raise ValueError('Order journal candidate binding changed')
    parts=[]
    for page in journal['pages']:
        file=path.parent/page['file']
        h=hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda:stream.read(2**20),b''):h.update(block)
        if h.hexdigest()!=page['sha256']:raise ValueError('Order journal changed')
        parts.append(pl.scan_parquet(file).filter(pl.col('candidate')==local).collect())
    if not parts:return
    orders=pl.concat(parts).with_columns(pl.when((pl.col('filled_quantity')+1.e-12<pl.col('requested_quantity')) | (pl.col('first_fill_clock')<0)).then(pl.lit('cancelled remainder')).otherwise(pl.lit('filled')).alias('order_status'))
    closed=[r for r in rows if r['status']=='closed']
    if not closed:return
    positions=pl.DataFrame([dict(listing=int(r['listings']),entry_clock=float(r['entry_clocks']),exit_clock=float(r['exit_clocks']),position=r['position']) for r in closed])
    assigned=orders.sort('first_fill_clock').join_asof(positions.sort('entry_clock'),
        left_on='first_fill_clock',right_on='entry_clock',by='listing',strategy='backward',check_sortedness=False)
    assigned=assigned.filter(pl.col('position').is_not_null() & (pl.col('completed_clock')<=pl.col('exit_clock')))
    groups=assigned.partition_by('position',as_dict=True)
    for row in rows:
        if row['status']!='closed':continue
        selected=groups.get((row['position'],),orders.head(0))
        buy=selected.filter(pl.col('kind').is_in([1,2]));sell=selected.filter(pl.col('kind').is_in([3,4]))
        bq=buy['filled_quantity'].sum();sq=sell['filled_quantity'].sum()
        row.update(buy_vwap=buy['filled_notional'].sum()/bq if bq else None,
            sell_vwap=sell['filled_notional'].sum()/sq if sq else None,
            bought_quantity=bq,sold_quantity=sq,fees=selected['fees'].sum(),
            order_count=len(selected),orders=selected.select(*[c for c in orders.columns if c not in ('candidate','listing')]).to_dicts())
