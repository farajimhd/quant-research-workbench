"""All-published-block release: full sealed-ledger reconciliation, sampled payload audit.

No source mutation, SQL, ClickHouse, data regeneration or liquidity resampling.
The certificate explicitly distinguishes full metadata from sampled fresh hashes.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
if __name__=='__main__':os.environ.setdefault('POLARS_MAX_THREADS','1')
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    __package__='research.bar_gpt.phase2_v1'

import argparse
from bisect import bisect_right
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import numpy as np
import polars as pl
import psutil
from ..v5.shards.contract import digest, file_hash, write_json
from ..v5.phase2_ar_shards.contract import BAR, BLOCK, validate_bars
from ..v5.phase2_ar_shards.storage import ShardReader
from ..v5.quote_quality_v1 import sanitize
from .data import sealed, beneath, Unit, plan_block

VERSION='bargpt-phase2-all-published-v1'
TERMINAL={'complete','inventory_exhausted','capacity_reached'}


def terminal(root):
    r=json.loads((root/'result.json').read_text())
    if r.get('state') not in TERMINAL or r.get('failure'):
        raise ValueError('All-published release requires a clean terminal root')
    p=json.loads((root/'progress.json').read_text()) if (root/'progress.json').exists() else r
    if p.get('state') not in TERMINAL or p.get('failure'):
        raise ValueError('Source root is active or failed')
    return r


def part(directory,item):
    p=beneath(directory,item['name'])
    if p.name!=item['name'] or p.stat().st_size!=item['bytes'] or file_hash(p)!=item['sha256']:
        raise ValueError('Ledger part identity/size/SHA changed')
    t=pl.read_parquet(p)
    if t.height!=item['rows']:raise ValueError('Ledger part row count changed')
    return t


def save_table(path,table):
    if path.exists():raise FileExistsError('Release index is immutable')
    if psutil.disk_usage(str(path.parent)).free<150*1024**3+2*table.estimated_size():
        raise OSError('Release metadata would consume disk reserve')
    temporary=path.with_suffix('.partial')
    with temporary.open('wb') as stream:
        table.write_parquet(stream,compression='zstd');stream.flush();os.fsync(stream.fileno())
    sha=file_hash(temporary);temporary.replace(path)
    if file_hash(path)!=sha:raise ValueError('Release index readback failed')
    return dict(name=path.name,rows=table.height,bytes=path.stat().st_size,sha256=sha)


def collect(root,output,number,*,workers=4,seconds=240):
    """Reconcile every published unit using authenticated checkpoint/journal parts.

    Checkpoint unit seals inherit the producer's receipt checks. Journal unit
    receipts are independently parsed/resealed here. No fresh full-tree hash claim.
    """
    began=time.monotonic();result=terminal(root);plan=sealed(root/'plan.json')
    if result['binding']!=plan['hash']:raise ValueError('Terminal plan binding changed')
    directory=root/'ledger-file-v1'
    original_head_sha=file_hash(directory/'HEAD.json')
    head=sealed(directory/'HEAD.json');manifest_path=beneath(directory,head['manifest'])
    if manifest_path.name!=head['manifest'] or file_hash(manifest_path)!=head['sha256']:
        raise ValueError('Ledger manifest identity changed')
    manifest=sealed(manifest_path)
    if manifest['binding']!=plan['hash']:raise ValueError('Foreign ledger binding')
    jobs={digest(j['ticker'])[:24]:j for j in plan['jobs']}
    classes={(k,int(y['year'])):int(y['liquid']) for k,j in jobs.items() for y in j['years']}
    records={}

    def add(path,seal,accounted,split,blocks,receipt=None):
        normalized=path.replace('\\','/')
        pieces=normalized.split('/')
        if len(pieces)!=3 or pieces[0]!='shards' or len(pieces[2])!=10:
            raise ValueError('Invalid published session path')
        key,day=pieces[1:];year=int(day[:4]);liquid=classes[(key,year)]
        if not 2019<=year<=2026 or split!=int(year==2026) or blocks<0 or accounted<0:
            raise ValueError('Published split/count/year changed')
        if receipt is not None:
            if (receipt.get('hash')!=seal or digest({k:v for k,v in receipt.items() if k!='hash'})!=seal
                    or receipt['binding']!=plan['hash'] or receipt['ticker']!=jobs[key]['ticker']
                    or receipt['liquid']!=liquid or receipt.get('session_date')!=day
                    or receipt['origins']!=blocks*BLOCK or len(receipt['splits'])!=blocks
                    or any(s!=split for s in receipt['splits'])
                    or digest(receipt['geometry'])!=digest(plan['geometry'])
                    or not receipt.get('price_basis')):
                raise ValueError('Published receipt/schema/basis/classification changed')
            names=[f['name'] for f in receipt['files']]
            if (len(set(names))!=len(names) or not {'origins.npy','pending.npy'}.issubset(names)
                    or sum(f['bytes'] for f in receipt['files'])!=receipt['bytes']
                    or accounted<receipt['bytes']):
                raise ValueError('Receipt file/count inventory changed')
        row=(normalized,seal,int(accounted),int(split),liquid,year,jobs[key]['ticker'],int(blocks))
        if normalized in records and records[normalized]!=row:
            raise ValueError('Conflicting repeated publication')
        records[normalized]=row

    checkpoints=manifest['checkpoint']
    block_parts=[part(directory,p) for p in checkpoints.get('blocks',[])]
    counts={}
    if block_parts:
        old=pl.concat(block_parts).with_columns(pl.col('path').str.replace_all('\\','/',literal=True))
        for path,n,lo,hi,unique in old.group_by('path').agg(
                pl.len().alias('n'),pl.col('ordinal').min().alias('lo'),
                pl.col('ordinal').max().alias('hi'),pl.col('ordinal').n_unique().alias('unique')).iter_rows():
            if lo!=0 or hi!=n-1 or unique!=n:raise ValueError('Checkpoint block ordinals changed')
            counts[path]=n
    for p in checkpoints.get('units',[]):
        for path,seal,size,split,_,_,_ in part(directory,p).iter_rows():
            add(path,seal,size,split,counts.get(path.replace('\\','/'),0))
    parts=[]
    for s in manifest.get('segments',[]):
        path=beneath(directory,s['name'])
        if path.name!=s['name'] or file_hash(path)!=s['sha256']:
            raise ValueError('Ledger segment SHA changed')
        parts.extend(sealed(path)['parts'])
    parts.extend(manifest['parts'])
    expected=1;checked=0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for first in range(0,len(parts),64):
            if time.monotonic()-began>seconds:
                raise TimeoutError('Bounded metadata audit expired; partial output is not certified')
            for t in pool.map(lambda p:part(directory,p),parts[first:first+64]):
                seq=t['sequence'].to_numpy()
                if not np.array_equal(seq,np.arange(expected,expected+len(t))):
                    raise ValueError('Ledger journal sequence changed')
                expected+=len(t);checked+=1
                for key,body,seal in t.filter(pl.col('kind')=='unit').select('key','body','seal').iter_rows():
                    b=json.loads(body)
                    if digest(b)!=seal or b['path']!=key:raise ValueError('Unit event seal/key changed')
                    r=b['receipt'];split=r['splits'][0] if r['splits'] else int(r['session_date'][:4]=='2026')
                    add(key,r['hash'],b['accounted_bytes'],split,r['blocks'],r)
            if checked%2048<64:print(json.dumps(dict(root=root.name,ledger_parts=checked,total_parts=len(parts),units=len(records))),flush=True)
    if expected-1!=manifest['sequence']:raise ValueError('Journal sequence coverage differs')
    table=pl.DataFrame(list(records.values()),schema={
        'path':pl.String,'receipt_hash':pl.String,'bytes':pl.Int64,'split':pl.Int64,
        'liquid':pl.Int64,'year':pl.Int64,'ticker':pl.String,'blocks':pl.Int64},orient='row').sort('path')
    if block_parts:
        related=old.join(table.select('path',pl.col('split').alias('unit_split'),
                                      pl.col('liquid').alias('unit_liquid')),on='path',how='left')
        if (related['unit_split'].null_count() or related.filter(
                (pl.col('split')!=pl.col('unit_split')) | (pl.col('liquid')!=pl.col('unit_liquid'))).height):
            raise ValueError('Checkpoint block/unit relationship changed')
    if (table.height!=result['units'] or table['bytes'].sum()!=result['bytes']
            or table['blocks'].sum()!=result['candidate_blocks']):
        raise ValueError('Full publication inventory does not reconcile with terminal result')
    catalog=sealed(root/'research-catalog.json')
    if catalog['binding']!=plan['hash'] or sum(s['blocks'] for s in catalog['streams'])!=result['candidate_blocks']:
        raise ValueError('Research catalog does not reconcile')
    actual={(t,y,bool(l)):n for t,y,l,n in table.group_by('ticker','year','liquid').agg(pl.col('blocks').sum()).iter_rows() if n}
    if actual!={(s['ticker'],s['year'],s['liquid']):s['blocks'] for s in catalog['streams']}:
        raise ValueError('Catalog and full ledger classification distributions differ')
    index=(table.select('path','split','liquid','blocks')
        .with_columns(pl.int_ranges(0,pl.col('blocks')).alias('ordinal')).explode('ordinal')
        .filter(pl.col('ordinal').is_not_null()).with_row_index('selection')
        .select('path','ordinal','split','liquid',pl.col('selection').cast(pl.Int64)))
    units=save_table(output/f'units-{number:02d}.parquet',table)
    blocks=save_table(output/f'blocks-{number:02d}.parquet',index)
    if file_hash(directory/'HEAD.json')!=original_head_sha:raise ValueError('Source HEAD bytes changed during audit')
    # Compare parsed identity after all reads, rather than trusting a stale pointer.
    if sealed(directory/'HEAD.json')!=head:raise ValueError('Source ledger changed during certification')
    report=dict(root=str(root),binding=plan['hash'],result_sha256=file_hash(root/'result.json'),
        plan_sha256=file_hash(root/'plan.json'),ledger_head_sha256=file_hash(directory/'HEAD.json'),
        ledger_manifest_sha256=head['sha256'],units=units,index=blocks,
        bytes=result['bytes'],candidate_blocks=result['candidate_blocks'],
        ledger_parts_hashed=checked+sum(len(v) for k,v in checkpoints.items() if k in ('units','blocks')),
        journal_rows=manifest['sequence'],unit_inventory=table.height,
        distribution=table.group_by('split','year','liquid').agg(
            pl.col('blocks').sum(),pl.col('bytes').sum(),pl.len().alias('publications'),
            pl.col('ticker').n_unique().alias('tickers')).sort('split','year','liquid').to_dicts(),
        ancestry=plan.get('continuation',{}).get('parents',[]),elapsed_seconds=time.monotonic()-began)
    return report,table


def sample_audit(root,table,binding):
    """One deterministic publication per split/liquidity stratum, all five views."""
    chosen=table.sort(['split','liquid','bytes','path']).group_by('split','liquid',maintain_order=True).tail(1)
    reports=[]
    for row in chosen.iter_rows(named=True):
        reader=ShardReader(beneath(root,row['path']),binding)
        if reader.receipt['hash']!=row['receipt_hash']:raise ValueError('Sample receipt differs from full inventory')
        for f in range(5):
            values=reader.view(f);validate_bars(values)
            v,m,_=sanitize(values['values'],values['valid'])
            if not np.array_equal(v,values['values']) or not np.array_equal(m,values['valid']):
                raise ValueError('Sample quote policy is not idempotent')
        origins=reader.array('origins.npy');reader.array('pending.npy')
        eligibility=[]
        for b in sorted({0,len(origins)//2,len(origins)-1}):
            if b>=0:eligibility.append(plan_block(reader,b).report)
        reports.append(dict(path=row['path'],ticker=row['ticker'],year=row['year'],split=row['split'],
                            liquid=row['liquid'],receipt_hash=row['receipt_hash'],
                            fresh_files_hashed=len(reader.arrays),eligibility=eligibility))
    return reports


def completed_report(output,number,root):
    """Reuse only a completed, SHA-pinned pass with unchanged source metadata."""
    path=output/f'root-{number:02d}.json'
    proof=json.loads(path.with_suffix('.proof.json').read_text())
    if file_hash(path)!=proof['sha256']:raise ValueError('Completed audit report changed')
    r=json.loads(path.read_text())
    if Path(r['root']).resolve()!=root:raise ValueError('Resume root/order changed')
    terminal(root)
    for name,key in [('plan.json','plan_sha256'),('result.json','result_sha256'),('ledger-file-v1/HEAD.json','ledger_head_sha256')]:
        if file_hash(root/name)!=r[key]:raise ValueError('Source changed since completed metadata audit')
    table=part(output,r['units']);index=part(output,r['index'])
    if (table.height!=r['unit_inventory'] or table['blocks'].sum()!=r['candidate_blocks']
            or table['bytes'].sum()!=r['bytes'] or index.height!=r['candidate_blocks']):
        raise ValueError('Completed audit index reconciliation changed')
    print(json.dumps(dict(root=root.name,reused_completed_metadata_audit=True)),flush=True)
    return r,table


def certify(roots,output,*,workers=4,seconds=240,resume=False):
    output=Path(output).resolve()
    if output.exists() and (not resume or (output/'certificate.json').exists()):
        raise FileExistsError('Use a new output, or explicitly resume an incomplete audit')
    output.mkdir(parents=True,exist_ok=resume)
    roots=[Path(r).resolve() for r in roots]
    if len(set(roots))!=len(roots):raise ValueError('Duplicate certification root')
    reports=[];tables=[];bindings={}
    for i,root in enumerate(roots):
        if resume and (output/f'root-{i:02d}.json').exists():
            report,table=completed_report(output,i,root)
        else:
            report,table=collect(root,output,i,workers=workers,seconds=seconds)
            path=output/f'root-{i:02d}.json';write_json(path,report)
            write_json(path.with_suffix('.proof.json'),dict(sha256=file_hash(path)))
        if report['binding'] in bindings.values():raise ValueError('Duplicate immutable plan')
        bindings[str(root)]=report['binding'];reports.append(report);tables.append(table)
    for r in reports:
        for p in r['ancestry']:
            if bindings.get(str(Path(p['root']).resolve()))!=p['binding']:
                raise ValueError('All published ancestor roots must be included with exact bindings')
    samples=[]
    for root,r,t in zip(roots,reports,tables):
        samples.extend(dict(root=str(root),**s) for s in sample_audit(root,t,r['binding']))
    all_units=pl.concat(tables)
    distribution=all_units.group_by('split','year','liquid').agg(
        pl.col('blocks').sum(),pl.col('bytes').sum(),pl.len().alias('publications'),
        pl.col('ticker').n_unique().alias('tickers')).sort('split','year','liquid').to_dicts()
    top=all_units.group_by('ticker').agg(pl.col('blocks').sum(),pl.col('bytes').sum()).sort('blocks',descending=True).head(20).to_dicts()
    certificate=dict(version=VERSION,policy='all published complete blocks; natural distribution; no 75/25 filter',
        evidence_class='full sealed-ledger metadata reconciliation plus stratified fresh payload audit',
        all_payload_hashes_recomputed=False,all_physical_files_restat=False,
        input_context_certified=True,latent_eligibility='checked per origin by phase2_v1; no padding',
        origins_count_semantics='stored overlapping presentations, not unique market events',
        roots=reports,bytes=sum(r['bytes'] for r in reports),blocks=sum(r['candidate_blocks'] for r in reports),
        publications=all_units.height,tickers=all_units['ticker'].n_unique(),distribution=distribution,
        top_tickers=top,sampled_publications=samples,created=time.time())
    certificate['hash']=digest(certificate);write_json(output/'certificate.json',certificate)
    return certificate


class ReleasedUnits(Sequence):
    """Compact sealed index; materialize paths/tuples only for units being read."""
    def __init__(self,groups):
        self.groups=groups;self.ends=[];total=0
        for root,binding,table in groups:
            total+=table.height;self.ends.append(total)

    def __len__(self):return self.ends[-1] if self.ends else 0

    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        group=bisect_right(self.ends,index);offset=self.ends[group-1] if group else 0
        root,binding,table=self.groups[group];row=table.row(index-offset,named=True)
        # Lexical safety was checked vectorially at load; real filesystem
        # containment is checked immediately BEFORE opening each used unit.
        relative=row['path'].replace('\\','/')
        return Unit(root,root.joinpath(*relative.split('/')),binding,
                    tuple(zip(row['ordinal'],row['split'],row['liquid'],row['selection'])),
                    row['receipt_hash'])

    def iter_from(self,split,start=0):
        offset=0
        for root,binding,table in self.groups:
            indices=table.with_row_index('_index').filter(
                (pl.col('_index')+offset>=start)&(pl.col('unit_split')==split))['_index']
            for i in indices:yield offset+int(i),self[offset+int(i)]
            offset+=table.height


def load_released_units(path):
    path=Path(path).resolve();cert=sealed(path)
    if cert.get('version')!=VERSION or cert.get('input_context_certified') is not True:
        raise ValueError('Unknown all-published certification policy')
    groups=[];count=0
    for r in cert['roots']:
        root=Path(r['root']).resolve();terminal(root)
        for name,key in [('plan.json','plan_sha256'),('result.json','result_sha256'),('ledger-file-v1/HEAD.json','ledger_head_sha256')]:
            if file_hash(root/name)!=r[key]:raise ValueError('Certified source metadata changed')
        table=part(path.parent,r['index']);inventory=part(path.parent,r['units'])
        if table.height!=r['candidate_blocks'] or inventory.height!=r['unit_inventory']:
            raise ValueError('Certified coverage changed')
        if table['path'].str.contains(r'(^[\\/]|(^|[\\/])\.\.([\\/]|$)|:)').any():
            raise ValueError('Unsafe certified payload path')
        grouped=table.sort('path','ordinal').group_by('path',maintain_order=True).agg(
            pl.col('ordinal'),pl.col('split'),pl.col('liquid'),pl.col('selection'))
        grouped=grouped.join(inventory.select('path','receipt_hash',pl.col('split').alias('unit_split')),
                             on='path',how='left',validate='1:1',maintain_order='left')
        if grouped.height!=inventory.height or grouped['receipt_hash'].null_count():
            raise ValueError('Certified index/unit inventory differs')
        groups.append((root,r['binding'],grouped))
        count+=table.height
    if count!=cert['blocks']:raise ValueError('Certified block count differs')
    return ReleasedUnits(groups),dict(policy=VERSION,certificate=str(path),hash=cert['hash'],blocks=count,bytes=cert['bytes'])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--seconds-per-root',type=int,default=240)
    p.add_argument('--resume',action='store_true',help='Reuse only SHA-proven completed root audits')
    a=p.parse_args(argv)
    if not 1<=a.workers<=4 or not 1<=a.seconds_per_root<=600:p.error('Bounded workers/time required')
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):p.error('Use the runtime root')
    if psutil.virtual_memory().available<64*1024**3:raise MemoryError('64GiB RAM reserve required')
    if os.name=='nt':
        own=psutil.Process();own.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS);own.ionice(psutil.IOPRIO_LOW)
    cert=certify(a.root,a.output,workers=a.workers,seconds=a.seconds_per_root,resume=a.resume)
    print(json.dumps(dict(certificate=str(a.output/'certificate.json'),hash=cert['hash'],bytes=cert['bytes'],
                          blocks=cert['blocks'],distribution=cert['distribution'])),flush=True)


if __name__=='__main__':main()
