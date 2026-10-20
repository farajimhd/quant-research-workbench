"""Training-only, deterministic sampling of sealed V32 initial evaluations.

No replay, local offspring, archives, validation metrics or old fitness enter
this pool. Recovery must reproduce the complete original population digest.
"""
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from .run_search import digest
from .publication import write_json
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def choose(metrics, quotas, rng):
    """Native array ranking and sampling; disjoint categories, fixed RNG order."""
    pnl=np.asarray(metrics['net_pnl'],dtype=np.float64)
    dd=np.asarray(metrics['drawdown'],dtype=np.float64)
    active=np.asarray(metrics['closed_positions'])>0
    valid=np.asarray(metrics['terminal_valid'],dtype=bool)
    if pnl.ndim!=1 or dd.shape!=pnl.shape or not np.isfinite(pnl).all() or not np.isfinite(dd).all():
        raise ValueError('Finite aligned initial metrics required')
    remaining=np.ones(len(pnl),bool);selected=[];labels=[]
    def take(mask,count,label,quality=None):
        indices=np.flatnonzero(mask & remaining)
        if quality is None:
            order=rng.permutation(len(indices))
        else:
            rank=np.empty(len(indices),np.int64)
            rank[np.lexsort((indices,-quality[indices]))]=np.arange(len(indices))
            # Weighted sampling without replacement, with nonzero support at
            # every rank. All arithmetic is outside the evolution RNG stream.
            weights=1./np.sqrt(1.+rank)
            keys=-np.log(np.maximum(rng.random(len(indices)),np.finfo(float).tiny))/weights
            order=np.argsort(keys,kind='stable')
        chosen=indices[order[:count]];remaining[chosen]=False
        selected.extend(chosen.tolist());labels.extend([label]*len(chosen))
    take(valid & (pnl>0),quotas[0],'profitable',pnl)
    take(valid & active & (pnl<=0),quotas[1],'promising_nonprofitable',pnl-.25*dd)
    take(remaining,quotas[2]+sum(quotas)-len(selected)-quotas[2],'random')
    if len(selected)!=sum(quotas):raise ValueError('Initial pool cannot fill session quota')
    return selected,labels


def recover_day(campaign,training,day,quotas,seed,output):
    """CPU-only exact recovery using V32's retained calibration and sampler."""
    from research.vectorized_backtest.v32.initial_population import initialize
    from research.vectorized_backtest.v32.calibration import load_probe
    from research.vectorized_backtest.v32.run_search import digest as old_digest
    campaign=Path(campaign);folder=campaign/'local'/day;started=perf_counter()
    seal_path=folder/'generation-0001'/'complete.json'
    seal=json.loads(seal_path.read_bytes());receipt_path=folder/'generation-0001'/day/'receipt.json'
    if file_hash(receipt_path)!=seal['receipt_sha256']:raise ValueError('Initial receipt seal changed')
    receipt=json.loads(receipt_path.read_bytes())
    if receipt['day']!=day or receipt['split']!='training' or receipt['validation_opened'] is not False or not receipt['full_session']:
        raise ValueError('Full training-only initial receipt required')
    if len(receipt['metrics']['net_pnl'])!=6144:raise ValueError('Sixfold initial evidence required')
    probe_path=campaign/'initialization-probes'/(day+'.npz')
    probe=load_probe(probe_path,training)
    contract=json.loads((folder/'checkpoint.json').read_bytes())['contract']
    if contract['seed']!=seed or contract['population']!=1024 or contract['initial_multiplier']!=6 or contract['initialization_probe_sha256']!=probe['sha256']:
        raise ValueError('Original initialization contract changed')
    rng=np.random.default_rng(np.random.SeedSequence([seed,int(day.replace('-',''))]))
    members,_=initialize(rng,6144,probe=probe)
    token=old_digest([m.payload() for m in members])
    if token!=seal['population_sha256'] or token!=receipt['population_sha256']:
        raise ValueError('Recovered initial population does not match sealed evaluation')
    sampling=np.random.default_rng(np.random.SeedSequence([seed,33,int(day.replace('-',''))]))
    indices,labels=choose(receipt['metrics'],quotas,sampling)
    # A deterministic reserve provides exact-identity deduplication refill.
    reserve=np.setdiff1d(np.arange(6144),indices,assume_unique=False)
    reserve=sampling.permutation(reserve)[:256].tolist()
    rows=[]
    from research.vectorized_backtest.v32.lineage import program_key
    for index,label in zip(indices+reserve,labels+['dedup_refill']*len(reserve)):
        rows.append(dict(index=index,category=label,key=program_key(members[index]),payload=members[index].payload(),
                         metrics={k:v[index] for k,v in receipt['metrics'].items()}))
    record=dict(day=day,rows=rows,selected_count=len(indices),quotas=list(quotas),population_sha256=token,
                receipt=str(receipt_path),receipt_sha256=seal['receipt_sha256'],seal=str(seal_path),seal_sha256=file_hash(seal_path),
                execution=receipt['execution'],identity=receipt['identity'],probe_sha256=probe['sha256'],
                recovery_seconds=perf_counter()-started,validation_opened=False)
    write_json(Path(output)/(day+'.json'),record)
    return dict(day=day,recovery_seconds=record['recovery_seconds'],path=str(Path(output)/(day+'.json')))


def assemble(records,seed=2020):
    """Keep balanced session quotas; replace duplicates from that session only."""
    seen=set();payload=[];origins=[];certificates={};categories={}
    for record in records:
        certificates[record['day']]=dict(path=record['seal'],sha256=record['seal_sha256'],receipt=record['receipt'],receipt_sha256=record['receipt_sha256'])
        added=0
        for row in record['rows']:
            if row['key'] in seen:continue
            seen.add(row['key']);payload.append(row['payload'])
            origins.append(dict(day=record['day'],index=row['index'],canonical_sha256=row['key'],category=row['category']))
            categories[row['category']]=categories.get(row['category'],0)+1;added+=1
            if added==record['selected_count']:break
        if added!=record['selected_count']:raise ValueError('Unique initial reserve exhausted')
    if len(payload)!=1024:raise ValueError('Exactly 1024 initial candidates required')
    from .run_search import source_hash
    pool=dict(version='v33-local-expert-pool-v1',source_sha256=source_hash(),population=payload,population_sha256=digest(payload),
              sources=[None]*len(payload),origins=origins,expert_count=0,fresh_count=len(payload),seed=seed,
              archive_certificates=certificates,categories=categories,validation_opened=False,initial_only=True)
    pool['pool_sha256']=digest(pool)
    return pool


def verify(pool,days,dataset=None):
    from .run_search import source_hash
    from .genome import Individual
    from .lineage import program_key
    if pool['source_sha256']!=source_hash() or pool.get('validation_opened') is not False or pool.get('initial_only') is not True:
        raise ValueError('Training-only initial source required')
    if digest({k:v for k,v in pool.items() if k!='pool_sha256'})!=pool['pool_sha256'] or digest(pool['population'])!=pool['population_sha256']:
        raise ValueError('Initial pool integrity changed')
    if set(pool['archive_certificates'])!=set(days) or len(pool['population'])!=1024 or len(pool['origins'])!=1024 or pool['sources']!=[None]*1024:
        raise ValueError('Initial training coverage changed')
    keys=[program_key(Individual.restore(p)) for p in pool['population']]
    if len(set(keys))!=1024 or keys!=[r['canonical_sha256'] for r in pool['origins']]:raise ValueError('Initial identity changed')
    for entry in pool['archive_certificates'].values():
        if file_hash(entry['path'])!=entry['sha256'] or file_hash(entry['receipt'])!=entry['receipt_sha256']:
            raise ValueError('Initial evidence changed')
