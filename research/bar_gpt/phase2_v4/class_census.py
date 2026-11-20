"""Read-only CPU census of eligible training labels, beside GPU training.

No Torch import, no model/checkpoint loads, no W&B writer. Resume is at a sealed
publication boundary, so interruption never double-counts a partially read unit.
"""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',
                  MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',POLARS_MAX_THREADS='1')
import argparse,json,sys,time
from pathlib import Path
import numpy as np
import polars as pl
import psutil
from .move_contract import CHANNELS,BINS,EDGES_PCT
from ..phase2_v2.data import load_units,checked_unit,plan_block
from ..phase2_v2.release import ReleasedUnits
from ..v5.shards.contract import digest,file_hash,write_json


def count_block(values,valid,ends,horizons):
    """Vectorized origin/horizon/channel census with the validation common mask."""
    y=values[ends[:,None]+horizons[None,:]].astype(np.float64)
    v=valid[ends[:,None]+horizons[None,:]].astype(bool)
    last=values[ends,None].astype(np.float64);lv=valid[ends,None].astype(bool)
    previous=values[ends[:,None]+horizons[None,:]-1].astype(np.float64)
    pv=valid[ends[:,None]+horizons[None,:]-1].astype(bool)
    mask=v&lv&pv&(y>0)&(last>0)&(previous>0)
    if not np.isfinite(y[mask]).all() or not np.isfinite(np.broadcast_to(last,y.shape)[mask]).all() or not np.isfinite(previous[mask]).all():
        raise ValueError('Nonfinite valid source value')
    offset=np.arange(len(horizons)*3).reshape(1,len(horizons),3)*11
    result=np.zeros((2,len(horizons),3,11),dtype=np.int64)
    for d,reference in enumerate((last,previous)):
        r=100*(y/np.where(mask,reference,1)-1)
        b=np.where(r==0,0,1+np.searchsorted(EDGES_PCT,np.abs(r),side='left')+5*(r>0))
        result[d]=np.bincount((offset+b)[mask],minlength=len(horizons)*3*11).reshape(len(horizons),3,11)
    return result,(~mask).sum(0)


def verify_source(revision):
    source=Path(__file__).resolve().parents[3]
    proof=Path('D:/TradingML/runtimes/bar_gpt/phase2-v4-phase1-head-20261119')/f'deployment-{revision[:9]}.json'
    saved=json.loads(proof.read_text())
    if saved['revision']!=revision:raise ValueError('Wrong deployment revision')
    for name,sha in saved['files'].items():
        if file_hash(source/name)!=sha:raise ValueError('Snapshot mutation: '+name)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--split',choices=('train','validation'),default='train')
    p.add_argument('--horizons',type=int,nargs='+',default=[1])
    p.add_argument('--io-mib-per-second',type=float,default=64);p.add_argument('--duty',type=float,default=.5)
    p.add_argument('--resume',action='store_true');p.add_argument('--profile-units',type=int,default=0)
    a=p.parse_args();a.output=a.output.resolve()
    if not a.output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority required')
    if not 0<a.duty<=1 or a.io_mib_per_second<=0 or a.profile_units<0:raise ValueError('Invalid resource bounds')
    if sorted(set(a.horizons))!=a.horizons or not all(1<=h<=64 for h in a.horizons):raise ValueError('Ordered unique horizons 1..64 required')
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise ValueError('Workstation required')
    a.output.mkdir(exist_ok=a.resume)
    if (a.output/'STOP').exists():raise RuntimeError('STOP preserved')
    import msvcrt
    lock=(a.output/'writer.lock').open('a+b')
    if not lock.tell():lock.write(b'0');lock.flush()
    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    own=psutil.Process()
    own.nice(psutil.IDLE_PRIORITY_CLASS);own.ionice(psutil.IOPRIO_VERYLOW)
    allowed=own.cpu_affinity();usage=psutil.cpu_percent(interval=.5,percpu=True)
    selected=min(allowed,key=lambda i:usage[i]);own.cpu_affinity([selected])
    write_json(a.output/'process.json',dict(pid=own.pid,created=own.create_time(),command=own.cmdline(),cpu=selected,
        priority='idle',io_priority='very_low',cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES']))
    verify_source(a.source_revision)
    units,identity=load_units(release=a.release)
    # Independent deterministic session shuffle makes early coverage less clustered;
    # a partial prefix is still labelled partial, never a full-population estimate.
    units=ReleasedUnits([(r,b,t.sample(fraction=1.,shuffle=True,seed=20261120+i)) for i,(r,b,t) in enumerate(units.groups)])
    split=int(a.split=='validation')
    total_units=sum(t.filter(pl.col('unit_split')==split).height for _,_,t in units.groups)
    total_blocks=sum(t.filter(pl.col('unit_split')==split)['ordinal'].list.len().sum() for _,_,t in units.groups)
    contract=dict(version='phase2-v4-class-census-v1',dataset=identity,split=a.split,horizons=a.horizons,
        bins=BINS,channels=CHANNELS,shuffle_seed=20261120,polars=pl.__version__,
        exposure='Each eligible training origin counted once per requested horizon; overlapping targets retained exactly as training',
        mask='Current/origin/previous close valid and positive; matches move metrics',source=a.source_revision)
    shape=(2,len(a.horizons),3,11)
    state=dict(contract=contract,next_unit=0,units=0,blocks=0,origins=0,rejected_origins=0,
        history_insufficient=0,target_outside_block=0,bytes=0,elapsed=0.,counts=np.zeros(shape,dtype=np.int64).tolist(),
        invalid=np.zeros((len(a.horizons),3),dtype=np.int64).tolist(),strata={})
    if a.resume:
        state=json.loads((a.output/'state.json').read_text())
        if state.get('hash')!=digest({k:v for k,v in state.items() if k!='hash'}):raise ValueError('Census state hash differs')
        if digest(state['contract'])!=digest(contract):raise ValueError('Census contract changed')
    counts=np.array(state['counts'],dtype=np.int64);invalid=np.array(state['invalid'],dtype=np.int64)
    strata={k:np.array(v,dtype=np.int64) for k,v in state['strata'].items()}
    began=time.monotonic();previous_elapsed=state['elapsed'];last_save=began
    session_bytes=0;session_origins=0;session_blocks=0
    def save(status,error=None):
        nonlocal last_save
        state.update(counts=counts.tolist(),invalid=invalid.tolist(),strata={k:v.tolist() for k,v in strata.items()},
                     elapsed=previous_elapsed+time.monotonic()-began)
        state['hash']=digest({k:v for k,v in state.items() if k!='hash'})
        write_json(a.output/'state.json',state)
        rate=state['blocks']/max(state['elapsed'],.001)
        result=dict(state=status,error=error,updated=time.time(),pid=own.pid,units_done=state['units'],units_total=total_units,
            blocks_done=state['blocks'],blocks_total=int(total_blocks),origins=state['origins'],
            completion_fraction=state['blocks']/max(total_blocks,1),blocks_per_second=rate,
            estimated_remaining_seconds=(total_blocks-state['blocks'])/rate if rate else None,
            horizon1_trade_counts=dict(zip(BINS,map(int,counts[0,0,0]))) if a.horizons[0]==1 else None,
            elapsed_seconds=state['elapsed'],rss_mib=own.memory_info().rss/2**20,
            duty=a.duty,io_mib_per_second=a.io_mib_per_second,torch_imported='torch' in sys.modules)
        write_json(a.output/'progress.json',result);last_save=time.monotonic()
        print(json.dumps(result),flush=True)
    save('running')
    try:
        for index,unit in units.iter_from(split,state['next_unit']):
            if (a.output/'STOP').exists():save('stopped');return
            unit_start=time.monotonic();io_before=own.io_counters().read_bytes;reader=None
            added=np.zeros(shape,dtype=np.int64);missing=np.zeros_like(invalid)
            tally=dict(origins=0,rejected_origins=0,history_insufficient=0,target_outside_block=0)
            try:
                reader=checked_unit(unit)
                if reader.receipt['bytes']>1024**3:raise MemoryError('Publication exceeds 1GiB census bound')
                raw=reader.view(0);close=raw['values'][:,[3,9,15]];valid=raw['valid'][:,[3,9,15]]
                if not np.isin(valid,(0,1)).all():raise ValueError('Invalid masks')
                if np.any(valid.astype(bool)&((close<=0)|~np.isfinite(close))):raise ValueError('Invalid positive close evidence')
                for ordinal,s,liquid,_ in unit.blocks:
                    if s!=split:raise ValueError('Mixed split publication')
                    plan=plan_block(reader,ordinal)
                    ends=plan.endpoints[0][:,-1]
                    c,m=count_block(close,valid,ends,np.asarray(a.horizons))
                    added+=c;missing+=m;tally['origins']+=len(ends)
                    for key,source in (('rejected_origins','rejected_union'),('history_insufficient','history_insufficient'),('target_outside_block','target_outside_block')):
                        tally[key]+=plan.report[source]
                key=f"{reader.receipt['session_date'][:4]}/liquid_{int(reader.receipt['liquid'])}"
                size=reader.receipt['bytes']
            finally:
                if reader is not None:
                    for arr in reader.arrays.values():
                        mm=getattr(arr,'_mmap',None)
                        if mm is not None:mm.close()
            counts+=added;invalid+=missing
            if key not in strata:strata[key]=np.zeros(shape,dtype=np.int64)
            strata[key]+=added
            for k,v in tally.items():state[k]+=v
            state['units']+=1;state['blocks']+=len(unit.blocks);state['bytes']+=size;state['next_unit']=index+1
            # Include hash+payload reads conservatively, plus observed logical I/O.
            session_bytes+=max(2*size,own.io_counters().read_bytes-io_before)
            session_origins+=tally['origins'];session_blocks+=len(unit.blocks)
            active=time.monotonic()-unit_start
            pause=max(active*(1/a.duty-1),session_bytes/(a.io_mib_per_second*2**20)-(time.monotonic()-began),0)
            end=time.monotonic()+pause
            while time.monotonic()<end:
                if (a.output/'STOP').exists():save('stopped');return
                time.sleep(min(1.,max(0,end-time.monotonic())))
            if time.monotonic()-last_save>=20:save('running')
            if a.profile_units and state['units']>=a.profile_units:save('profile_complete');return
        save('complete')
    except BaseException as exc:
        save('failed',repr(exc));raise


if __name__=='__main__':main()
