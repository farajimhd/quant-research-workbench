"""Verified historical session archives and globally namespaced pedigree."""
import json
from pathlib import Path
import numpy as np
from .genome import Individual
from .identity import digest
from .lineage import program_key
from .local_runner import CompactPedigree
from .ownership import round_robin
from research.vectorized_backtest.v42.distributed_assets import sha


def session_export(root,day,job,capacity=128):
    root=Path(root);folder=root/'phase-1/local'/day;cp=json.loads((folder/'checkpoint.json').read_bytes());state=cp['state'];contract=cp['contract']
    final=json.loads((folder/'complete.json').read_bytes())
    if digest(state)!=cp['state_sha256'] or state['completed']!=job['phase1_generations'] or contract['source_sha256']!=job['source_sha256'] or contract['objective']!=job['objective']:raise ValueError('Unsealed/different session expert contract')
    if sha(folder/'checkpoint.json')!=final['checkpoint_sha256']:raise ValueError('Session final checkpoint changed')
    records={}
    for r in state['archive_pool']+state['archive']:
        if r['score']<=0:continue
        if r['canonical_sha256'] not in records or r['score']>records[r['canonical_sha256']]['score']:records[r['canonical_sha256']]=r
    chosen=sorted(records.values(),key=lambda r:(-r['score'],r['serial']))[:capacity]
    wanted={r['serial'] for r in chosen};pedigree={r['serial']:r for r in state['pedigree'] if r['serial'] in wanted}
    promoted=json.loads((root/'phase1-promotion.json').read_bytes())
    pedigree.update({r['serial']:r for r in promoted['pedigree'] if r['serial'] in wanted})
    scores={};seals={}
    for g,h in state['seals'].items():
        f=folder/f'generation-{int(g):04d}';seal=json.loads((f/'complete.json').read_bytes())
        if sha(f/'complete.json')!=h or sha(f/'births.json')!=seal['births_sha256']:raise ValueError('Session ancestry seal changed')
        for r in json.loads((f/'births.json').read_bytes()):
            if r['serial'] in wanted:pedigree[r['serial']]=r
        if any(r['generation']==int(g) for r in chosen):
            if sha(f/'scores.json')!=seal['scores_sha256'] or sha(f/day/'receipt.json')!=seal['receipt_sha256']:raise ValueError('Session expert evaluation changed')
            score=json.loads((f/'scores.json').read_bytes());scores[int(g)]={int(i):float(v) for i,v in zip(score['ids'],score['scores'])};seals[int(g)]=h
    out=[]
    for r in chosen:
        m=Individual.restore(r['payload'])
        if m.case_owner is not None or program_key(m)!=r['canonical_sha256'] or r['serial'] not in pedigree:raise ValueError('Unverified session expert genotype/pedigree')
        if not np.isclose(scores[r['generation']][r['serial']],r['score'],rtol=1e-10,atol=1e-7):raise ValueError('Expert source score changed')
        out.append(dict(r,pedigree=pedigree[r['serial']],source_seal_sha256=seals[r['generation']]))
    return dict(day=day,source_sha256=job['source_sha256'],checkpoint_sha256=sha(folder/'checkpoint.json'),objective=contract['objective'],records=out,validation_opened=False)


def global_seed(exports,job,probe):
    population=job['population'];expert_slots=population-int(np.ceil(.25*population));groups={d:list(range(len(e['records']))) for d,e in exports.items()}
    # Round robin across all training sessions, canonical dedup after ownership removal.
    ordered=[(d,i) for rank in range(max(map(len,groups.values()),default=0)) for d in sorted(groups) if rank<len(groups[d]) for i in [groups[d][rank]]]
    chosen=[];seen=set()
    for day,i in ordered:
        r=exports[day]['records'][i];key=r['canonical_sha256']
        if key in seen:continue
        seen.add(key);chosen.append((day,r))
        if len(chosen)==expert_slots:break
    if not chosen:raise ValueError('No verified positive session experts; refusing a fresh global campaign')
    identities=set()
    for day,r in chosen:
        identities.add((day,r['serial']))
        identities.update((day,int(a)) for a,_ in r['pedigree']['ancestors'])
        identities.update((day,int(a)) for a in r['pedigree']['parents'] if a>=0)
    mapping={key:i for i,key in enumerate(sorted(identities))};records=[];members=[];ids=[];sources=[]
    for day,r in chosen:
        old=r['pedigree'];serial=mapping[(day,r['serial'])];record=dict(old,serial=serial,parents=[mapping[(day,int(p))] if p>=0 else -1 for p in old['parents']],ancestors=[[mapping[(day,int(a))],distance] for a,distance in old['ancestors']],case_owner=None,source_day=day,source_serial=r['serial'],source_generation=r['generation'])
        records.append(record);members.append(Individual.restore(r['payload']));ids.append(serial);sources.append(dict(day=day,serial=r['serial'],generation=r['generation'],source_seal_sha256=r['source_seal_sha256']))
    pedigree=CompactPedigree(3,records,len(mapping));rng=np.random.default_rng(job['seed'])
    from research.vectorized_backtest.v42.initial_population import initialize
    import os
    os.environ.pop('V42_INITIAL_POOL',None)
    attempts=0
    while len(members)<population:
        attempts+=1
        if attempts>8:raise ValueError('Unique global fresh initialization exhausted')
        batch,report=initialize(rng,max(10,population-len(members)),probe=probe)
        for index,original in enumerate(batch):
            m=Individual.restore(original.payload());key=program_key(m)
            if key in seen:continue
            seen.add(key);members.append(m);fresh_ids=pedigree.birth([m],1,[(-1,-1)],['fresh-global-exploration']);ids.extend(fresh_ids.tolist());sources.append(None)
            from research.vectorized_backtest.v42.family_stats import seed
            seed(pedigree,fresh_ids,[report.get('initial_families',['random']*len(batch))[index]])
            if len(members)==population:break
    return dict(population=[m.payload() for m in members],ids=ids,pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,
        sources=sources,expert_count=len(chosen),fresh_count=population-len(chosen),validation_opened=False,source_sha256=job['source_sha256'])
