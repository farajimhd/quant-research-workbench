"""Session founders and balanced, ancestry-preserving global promotion."""
import numpy as np
from .identity import digest
from .genome import Individual
from .lineage import program_key
from .local_runner import CompactPedigree

def seed(exports,job,probe):
    rng=np.random.default_rng(np.random.SeedSequence([job['seed'],int(job['training_days'][0].replace('-',''))]) if job['campaign_stage']=='session' else job['seed'])
    members=[];records=[];ids=[];sources=[];seen=set()
    if job['campaign_stage']=='global':
        if set(exports)!=set(job['training_days']):raise ValueError('All 24 session exports required before global start')
        chosen=[]
        for rank in range(job['expert_capacity_per_session']):
            for day in sorted(exports):
                e=exports[day]
                if e['source_sha256']!=job['source_sha256'] or e['objective']!=job['objective'] or e['validation_opened']:raise ValueError('Expert authority changed')
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
        from research.vectorized_backtest.v42.initial_population import initialize_random
        fresh,report=initialize_random(rng,6144-len(members),probe=probe)
        fresh=[Individual.restore(m.payload()) for m in fresh];families=['random']*len(fresh)
    else:
        if exports:raise ValueError('Session stage cannot import another session')
        from .initial_population import initialize
        fresh,report=initialize(rng,6144,probe=probe);families=report['initial_families'];pedigree=CompactPedigree(3);expert_count=0
    fresh_ids=pedigree.birth(fresh,1,[(-1,-1)]*len(fresh),['fresh']*len(fresh))
    from research.vectorized_backtest.v42.family_stats import seed as families_seed
    families_seed(pedigree,fresh_ids,families)
    members.extend(fresh);ids.extend(fresh_ids.tolist());sources.extend([None]*len(fresh))
    if len(members)!=6144 or len({program_key(m) for m in members})!=6144:raise ValueError('Unique 6144-candidate initial cohort required')
    report=__import__('json').loads(__import__('json').dumps(report))
    return dict(population=[m.payload() for m in members],ids=ids,pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,initialization=report,expert_count=expert_count,fresh_count=len(fresh),sources=sources,validation_opened=False,source_sha256=job['source_sha256'])
