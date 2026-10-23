"""Certified V42 local archives plus calibrated fresh candidates seed Phase 2."""
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


def seed_pool(phase1,days,population_size=1024,fresh_fraction=.25,seed=2020,dataset=None,*,probe=None,historical=False,evidence_share=None):
    if not 0<fresh_fraction<1:raise ValueError('Positive fresh/expert fractions required')
    archives={};certificates={};pools={};checkpoints={}
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
        pools[day]=state['archive_pool'];checkpoints[day]=checkpoint
    if historical:
        from .phase2_screen import historical_experts,verify_record,POLICY
        if len(days)!=24 or population_size!=3072 or fresh_fraction!=.25:raise ValueError('Explicit 24-session 3x screening required')
        experts,sampling=historical_experts(pools,archives,seed)
        evidence_cache={}
        for r in experts:
            folder=Path(phase1)/r['day']
            if evidence_share:
                from .distributed_assets import remote
                other=remote(folder,evidence_share)
                if (other/f"generation-{r['generation']:04d}"/'complete.json').exists():folder=other
            verify_record(folder,checkpoints[r['day']],r,evidence_cache)
    else:
        slots=population_size-int(np.ceil(population_size*fresh_fraction));experts=balanced_experts(archives,slots)
    payload=[r['payload'] for r in experts];sources=[{k:r[k] for k in ['day','serial','ancestry','canonical_sha256']} for r in experts]
    if historical:
        for source,r in zip(sources,experts):source['generation']=r['generation']
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
    pool=dict(version='v42-local-expert-pool-v1',source_sha256=source_hash(),population=payload,population_sha256=digest(payload),sources=sources,phase1_root=str(Path(phase1)),
              expert_count=len(experts),fresh_count=population_size-len(experts),archive_certificates=certificates,validation_opened=False,
              fresh_initialization_reports=fresh_reports,calibration_probe_sha256=probe['sha256'])
    # JSON object keys are strings after reload. Hash the persisted shape so
    # integer-keyed sampling histograms cannot invalidate an exact resume.
    pool=json.loads(json.dumps(pool,allow_nan=False))
    if historical:
        from . import phase2_screen
        pool.update(historical_policy=POLICY,sampling=sampling,policy_files_sha256=dict(phase2=file_hash(__file__),screen=file_hash(phase2_screen.__file__)))
    pool['pool_sha256']=digest(pool);return pool


def verify_pool(pool,days,dataset=None):
    from .phase2_screen import POLICY
    if pool.get('historical_policy') not in (None,POLICY):raise ValueError('Unknown historical seeding policy')
    if pool.get('historical_policy'):
        from . import phase2_screen
        if pool['policy_files_sha256']!=dict(phase2=file_hash(__file__),screen=file_hash(phase2_screen.__file__)):raise ValueError('Historical policy implementation changed')
        if 'screen' in pool:phase2_screen.verify_screen(pool)
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
        rows=state['archive_pool'] if pool.get('historical_policy') else json.loads((folder/'archive.json').read_bytes())
        records[day]={r['canonical_sha256']:r for r in rows}
    seen=set();experts=0
    if digest(pool['population'])!=pool['population_sha256']:raise ValueError('Seed population changed')
    for payload,source in zip(pool['population'],pool['sources']):
        key=program_key(Individual.restore(payload))
        if key in seen:raise ValueError('Duplicate global seed')
        seen.add(key)
        if source is not None:
            experts+=1;r=records[source['day']][key]
            fields=('day','serial','ancestry','canonical_sha256','generation') if pool.get('historical_policy') else ('day','serial','ancestry','canonical_sha256')
            if source!={k:r[k] for k in fields} or payload!=r['payload']:raise ValueError('Global source ancestry changed')
    if experts!=pool['expert_count'] or len(seen)-experts!=pool['fresh_count']:raise ValueError('Seed expert/fresh counts changed')


def run_global(training,validation,phase1,output,population_size=1024,generations=50,seed=2020,predecessor_job=None,predecessor_proof=None,batch_size=512,session_workers=1,capture_gib=1.,selection=None,host_reserve_gib=128.,host_admission_gib=32.,*,initialization_probe=None,host_prefetch=False,shared_replay=False,distributed_job=None):

    from .run_search import bind_dataset
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    output=require_runtime(output);dataset=bind_dataset(training,'training');path=output/'seed-pool.json'
    from .calibration import load_probe
    if initialization_probe is None:raise ValueError('Global seed calibration probe required')
    probe=load_probe(initialization_probe,training)
    if path.exists():
        pool=json.loads(path.read_bytes());verify_pool(pool,dataset['days'],dataset=dataset)
        if pool.get('screen',{}).get('initial_count')!=3072:raise ValueError('Existing global pool lacks authorized 3x screening')
        if pool['calibration_probe_sha256']!=probe['sha256']:raise ValueError('Global seed calibration changed')
    else:
        from .phase2_screen import screen
        if population_size!=1024:raise ValueError('V42 3x screen requires steady population 1024')
        candidates=output/'initial-screen-pool.json'
        if candidates.exists():
            wide=json.loads(candidates.read_bytes());verify_pool(wide,dataset['days'],dataset=dataset)
            if wide['calibration_probe_sha256']!=probe['sha256']:raise ValueError('Screen calibration changed')
        else:
            share=json.loads(Path(distributed_job).read_bytes())['workstation_share'] if distributed_job else None
            wide=seed_pool(phase1,dataset['days'],3072,seed=seed,dataset=dataset,probe=probe,historical=True,evidence_share=share);write_json(candidates,wide)
        pool=screen(wide,training,output,population_size=population_size,batch_size=batch_size,session_workers=session_workers,capture_gib=capture_gib,
                    host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,distributed_job=distributed_job,shared_replay=shared_replay)
        write_json(path,pool)
    return run(training,validation,output,population_size=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,seed=seed,
               selection=Selection() if selection is None else selection,initial_population=pool,maximum_gate_gib=8.,capture_gib=capture_gib,
               predecessor_job=predecessor_job,predecessor_proof=predecessor_proof,host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,host_prefetch=host_prefetch,
               shared_replay=shared_replay,distributed_job=distributed_job,replay_width_workers=1 if shared_replay or distributed_job else None)
