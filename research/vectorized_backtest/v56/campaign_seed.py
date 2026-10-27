"""Session founders and balanced, ancestry-preserving global promotion."""
import numpy as np
from .identity import digest
from .genome import Individual
from .lineage import program_key
from .local_runner import CompactPedigree

def seed(exports,job,probe,progress=None):
    rng=np.random.default_rng(np.random.SeedSequence([job['seed'],int(job['training_days'][0].replace('-',''))]) if job['campaign_stage']=='session' else job['seed'])
    members=[];records=[];ids=[];sources=[];seen=set()
    if job['campaign_stage']=='global':
        if set(exports)!=set(job['training_days']):raise ValueError('All 24 session exports required before global start')
        from .resume_authority import lineage_jobs
        allowed_sources={item['source_sha256'] for item in lineage_jobs(job)}
        chosen=[]
        for rank in range(job['expert_capacity_per_session']):
            for day in sorted(exports):
                e=exports[day]
                if e['source_sha256'] not in allowed_sources or e['objective']!=job['objective'] or e['validation_opened']:raise ValueError('Expert authority changed')
                if rank>=len(e['records']):continue
                row=e['records'][rank];m=Individual.restore(row['payload']);key=program_key(m)
                if m.case_owner is not None or key!=row['program_sha256']:raise ValueError('Invalid promoted program')
                if key in seen:continue
                seen.add(key);chosen.append((day,row,m))
        identities=set()
        for day,row,m in chosen:
            old=row['pedigree'];identities.add((day,old['serial']))
            identities.update((day,int(i)) for i,d in old['ancestors']);identities.update((day,int(i)) for i in old['parents'] if i>=0)
        mapping={k:i for i,k in enumerate(sorted(identities))}
        for day,row,m in chosen:
            old=row['pedigree'];serial=mapping[(day,old['serial'])]
            rec=dict(old,serial=serial,generation=1,operation='session-expert',parents=[mapping[(day,int(i))] if i>=0 else -1 for i in old['parents']],ancestors=[[mapping[(day,int(i))],d] for i,d in old['ancestors']],source_day=day,source_serial=old['serial'],source_generation=row['generation'])
            rec['label']=f"G001-{serial:06d}-session-{day}-{row['serial']}"
            members.append(m);records.append(rec);ids.append(serial);sources.append(dict(day=day,serial=row['serial'],score=row['score'],export_sha256=exports[day]['export_sha256']))
        pedigree=CompactPedigree(3,records,len(mapping));expert_count=len(members)
        if job.get('global_initialization',{}).get('version')=='v56-blind-mix-v1':
            from .blind_mix import reproduce
            by_day={day:[] for day in sorted(exports)}
            for index,(day,row,m) in enumerate(chosen):
                serial=ids[index];by_day[day].append((serial,m));rec=pedigree.records[serial]
                rec.update(global_founder=True,founder_index=index,founder_mask=hex(1<<index),founder_groups=['session-expert'],founder_methods=['session-expert'],source_sessions=[day])
            fresh,blind,report=reproduce(by_day,job['seed'],progress=progress);families=['blind-reproduction']*len(fresh)
        else:
            from research.vectorized_backtest.v42.initial_population import initialize_random
            fresh,report=initialize_random(rng,6144-len(members),probe=probe)
            fresh=[Individual.restore(m.payload()) for m in fresh];families=['random']*len(fresh)
    else:
        if exports:raise ValueError('Session stage cannot import another session')
        from .initial_population import initialize
        fresh,report=initialize(rng,6144,probe=probe);families=report['initial_families'];pedigree=CompactPedigree(3);expert_count=0
    blind_mode=job.get('campaign_stage')=='global' and job.get('global_initialization',{}).get('version')=='v56-blind-mix-v1'
    fresh_ids=pedigree.birth(fresh,1,[(-1,-1)]*len(fresh),['blind-reproduction' if blind_mode else 'fresh']*len(fresh))
    from research.vectorized_backtest.v42.family_stats import seed as families_seed
    families_seed(pedigree,fresh_ids,families)
    if job.get('campaign_stage')=='global' and job.get('global_initialization',{}).get('version')=='v56-blind-mix-v1':
        for index,(serial,meta) in enumerate(zip(fresh_ids,blind),expert_count):
            from pathlib import Path
            from research.vectorized_backtest.v42.publication import write_json
            compact={k:v for k,v in meta.items() if k not in ('steps','fragments')};compact['operators']=sorted({'whole:'+s['mode'] for s in meta['steps']})
            if job.get('campaign'):
                path=Path(job['campaign'])/'phase-2/global/founder-provenance'/f'{serial}.json';write_json(path,meta)
                compact['provenance_sha256']=digest(meta);compact['provenance_file']=str(path)
            pedigree.records[int(serial)].update(global_founder=True,founder_index=index,founder_mask=hex(1<<index),founder_groups=[meta['group']],founder_methods=[meta['method']],source_sessions=meta['source_sessions'],blind_mix=compact)
    members.extend(fresh);ids.extend(fresh_ids.tolist());sources.extend([None]*len(fresh))
    if len(members)!=6144 or len({program_key(m) for m in members})!=6144:raise ValueError('Unique 6144-candidate initial cohort required')
    report=__import__('json').loads(__import__('json').dumps(report))
    return dict(population=[m.payload() for m in members],ids=ids,pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,initialization=report,expert_count=expert_count,fresh_count=0 if blind_mode else len(fresh),blind_count=len(fresh) if blind_mode else 0,sources=sources,validation_opened=False,source_sha256=job['source_sha256'])
