"""Certified V31 local archives plus calibrated fresh candidates seed Phase 2."""
import json
from pathlib import Path
import numpy as np
from .archive import balanced_experts
from .genome import Individual
from .initial_population import initialize
from .lineage import program_key,Selection
from .run_search import digest,run
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,write_json


def attach_source_ancestry(pedigree,sources):
    # One phase boundary counts as a pedigree link; tagged local IDs cannot
    # collide across dates or with nonnegative Phase-2 population IDs.
    def tag(day,serial):return -(1+(int(day.replace('-',''))<<32)+int(serial))
    for i,source in enumerate(sources):
        if source is None:continue
        ancestors={tag(source['day'],source['serial']):1}
        for ancestor,distance in source['ancestry']:
            if distance<pedigree.depth:ancestors[tag(source['day'],ancestor)]=distance+1
        pedigree.records[i]['ancestors']=sorted(ancestors.items());pedigree.records[i]['local_origin']=source


def seed_pool(phase1,days,population_size=1024,fresh_fraction=.25,seed=2020,dataset=None,*,probe=None):
    if not 0<fresh_fraction<1:raise ValueError('Positive fresh/expert fractions required')
    archives={};certificates={}
    for day in days:
        folder=Path(phase1)/day;seal=json.loads((folder/'complete.json').read_bytes())
        if seal['status']!='complete' or seal['day']!=day or seal['validation_opened'] is not False:raise ValueError('Completed training-session archive required')
        if file_hash(folder/'archive.json')!=seal['archive_sha256'] or file_hash(folder/'checkpoint.json')!=seal['checkpoint_sha256']:raise ValueError('Archive evidence changed')
        checkpoint=json.loads((folder/'checkpoint.json').read_bytes());contract=checkpoint['contract'];state=checkpoint['state']
        if digest(state)!=checkpoint['state_sha256'] or state['completed']!=contract['generations'] or contract['day']!=day or contract['validation_opened'] is not False:
            raise ValueError('Incomplete or invalid local checkpoint')
        from .run_search import source_hash
        if contract['source_sha256']!=source_hash():raise ValueError('Local experts use a different source')
        if dataset is not None:
            pinned={k:dataset.get(k) for k in ('inputs','history','indicator_root','enriched_root')}
            pinned.update(input_receipts=dataset['receipts'],indicator_receipts=dataset['indicator_receipts'],enriched_receipts=dataset['enriched_receipts'])
            if contract['dataset']!=pinned:raise ValueError('Local expert dataset differs from global training')
        records=json.loads((folder/'archive.json').read_bytes())
        if records!=state['archive']:raise ValueError('Published archive differs from sealed local state')
        for record in records:
            if record['day']!=day or program_key(Individual.restore(record['payload']))!=record['canonical_sha256']:raise ValueError('Archive candidate identity changed')
        archives[day]=records;certificates[day]=file_hash(folder/'complete.json')
    slots=population_size-int(np.ceil(population_size*fresh_fraction));experts=balanced_experts(archives,slots)
    payload=[r['payload'] for r in experts];sources=[{k:r[k] for k in ['day','serial','ancestry','canonical_sha256']} for r in experts]
    seen={r['canonical_sha256'] for r in experts};rng=np.random.default_rng(seed)
    # Fill all unused slots with unique fresh candidates; never relax eligibility.
    if probe is None:raise ValueError('Global fresh candidates require a training-only calibration probe')
    attempts=0
    fresh_reports=[]
    while len(payload)<population_size:
        attempts+=1
        if attempts>8:raise ValueError('Unique global seed budget exhausted')
        fresh,report=initialize(rng,max(10,population_size-len(payload)),probe=probe);fresh_reports.append(report)
        for m in fresh:
            key=program_key(m)
            if key in seen:continue
            seen.add(key);payload.append(m.payload());sources.append(None)
            if len(payload)==population_size:break
    from .run_search import source_hash
    pool=dict(version='v31-local-expert-pool-v1',source_sha256=source_hash(),population=payload,population_sha256=digest(payload),sources=sources,phase1_root=str(Path(phase1)),
              expert_count=len(experts),fresh_count=population_size-len(experts),archive_certificates=certificates,validation_opened=False,
              fresh_initialization_reports=fresh_reports,calibration_probe_sha256=probe['sha256'])
    pool['pool_sha256']=digest(pool);return pool


def verify_pool(pool,days,dataset=None):
    from .run_search import source_hash
    if pool.get('source_sha256')!=source_hash():raise ValueError('Global seed source changed')
    if pool.get('validation_opened') is not False or digest({k:v for k,v in pool.items() if k!='pool_sha256'})!=pool['pool_sha256']:raise ValueError('Global seed provenance changed')
    if set(pool['archive_certificates'])!=set(days) or len(pool['sources'])!=len(pool['population']):raise ValueError('Global seed training coverage changed')
    records={}
    for day,hashvalue in pool['archive_certificates'].items():
        folder=Path(pool['phase1_root'])/day;seal=json.loads((folder/'complete.json').read_bytes())
        if file_hash(folder/'complete.json')!=hashvalue or file_hash(folder/'archive.json')!=seal['archive_sha256'] or file_hash(folder/'checkpoint.json')!=seal['checkpoint_sha256']:raise ValueError('Seed archive seal changed')
        checkpoint=json.loads((folder/'checkpoint.json').read_bytes());contract=checkpoint['contract'];state=checkpoint['state']
        if contract['source_sha256']!=source_hash() or digest(state)!=checkpoint['state_sha256'] or state['completed']!=contract['generations'] or contract['day']!=day or contract['validation_opened'] is not False:raise ValueError('Seed local checkpoint identity changed')
        if dataset is not None:
            pinned={k:dataset.get(k) for k in ('inputs','history','indicator_root','enriched_root')}
            pinned.update(input_receipts=dataset['receipts'],indicator_receipts=dataset['indicator_receipts'],enriched_receipts=dataset['enriched_receipts'])
            if contract['dataset']!=pinned:raise ValueError('Seed dataset differs from global training')
        records[day]={r['canonical_sha256']:r for r in json.loads((folder/'archive.json').read_bytes())}
    seen=set();experts=0
    if digest(pool['population'])!=pool['population_sha256']:raise ValueError('Seed population changed')
    for payload,source in zip(pool['population'],pool['sources']):
        key=program_key(Individual.restore(payload))
        if key in seen:raise ValueError('Duplicate global seed')
        seen.add(key)
        if source is not None:
            experts+=1;r=records[source['day']][key]
            if source!={k:r[k] for k in ('day','serial','ancestry','canonical_sha256')} or payload!=r['payload']:raise ValueError('Global source ancestry changed')
    if experts!=pool['expert_count'] or len(seen)-experts!=pool['fresh_count']:raise ValueError('Seed expert/fresh counts changed')


def run_global(training,validation,phase1,output,population_size=1024,generations=50,seed=2020,predecessor_job=None,predecessor_proof=None,batch_size=512,session_workers=1,capture_gib=1.,selection=None,host_reserve_gib=128.,host_admission_gib=32.,*,initialization_probe=None,host_prefetch=False):

    from .run_search import bind_dataset
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    output=require_runtime(output);dataset=bind_dataset(training,'training');path=output/'seed-pool.json'
    from .calibration import load_probe
    if initialization_probe is None:raise ValueError('Global seed calibration probe required')
    probe=load_probe(initialization_probe,training)
    if path.exists():
        pool=json.loads(path.read_bytes());verify_pool(pool,dataset['days'],dataset=dataset)
        if pool['calibration_probe_sha256']!=probe['sha256']:raise ValueError('Global seed calibration changed')
    else:
        pool=seed_pool(phase1,dataset['days'],population_size,seed=seed,dataset=dataset,probe=probe);write_json(path,pool)
    return run(training,validation,output,population_size=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,seed=seed,
               selection=Selection() if selection is None else selection,initial_population=pool,maximum_gate_gib=8.,capture_gib=capture_gib,
               predecessor_job=predecessor_job,predecessor_proof=predecessor_proof,host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,host_prefetch=host_prefetch)
