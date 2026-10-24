"""Sealed, all-generation phase0 specialist promotion; never import fitness."""
from dataclasses import replace
import json
from pathlib import Path
import numpy as np
from .genome import Individual,sample
from .identity import digest
from .lineage import program_key
from .local_runner import CompactPedigree
from .ownership import round_robin
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v42.publication import write_json


def promote(source,output,target=1024,expected_generations=30):
    source=Path(source);output=Path(output);cp=source/'checkpoint.json'
    saved=json.loads(cp.read_bytes());state=saved['state'];contract=saved['contract']
    if digest(state)!=saved['state_sha256'] or state['completed']!=expected_generations or contract['phase']!=0:raise ValueError('Incomplete phase0 checkpoint')
    final=json.loads((source/'complete.json').read_bytes())
    if file_hash(cp)!=final['checkpoint_sha256']:raise ValueError('Phase0 final seal changed')
    seals={};scores={}
    for g in range(1,expected_generations+1):
        folder=source/f'generation-{g:04d}';sealpath=folder/'complete.json'
        if file_hash(sealpath)!=state['seals'][str(g)]:raise ValueError('Historical phase0 seal changed')
        seal=json.loads(sealpath.read_bytes())
        for path,key in ((folder/contract['day']/'receipt.json','receipt_sha256'),(folder/'scores.json','scores_sha256'),(folder/'births.json','births_sha256'),(folder/'specialists.json','specialists_sha256')):
            if file_hash(path)!=seal[key]:raise ValueError('Historical phase0 evidence changed')
        if seal['phase']!=0 or not seal['ownership_verified']:raise ValueError('Unverified source ownership')
        score=json.loads((folder/'scores.json').read_bytes());scores[g]={int(i):float(v) for i,v in zip(score['ids'],score['scores'])};seals[g]=file_hash(sealpath)
    candidates=state['specialist_archive'];groups={}
    for i,row in enumerate(candidates):
        owner=row['case_owner'];member=Individual.restore(row['payload'])
        if owner!=member.case_owner or row['pedigree']['case_owner']!=owner:raise ValueError('Archive owner changed')
        index=contract['case_inventory'].index(owner);value=float(row['case_scores'][index]);generation=row['source_generation']
        if not value>0 or not np.isclose(scores[generation][row['serial']],value,rtol=1e-10,atol=1e-7):raise ValueError('Specialist score/source mismatch')
        groups.setdefault(owner,[]).append(i)
    for indexes in groups.values():indexes.sort(key=lambda i:(-max(candidates[i]['case_scores']),candidates[i]['serial']))
    order=round_robin(groups,1);chosen=[];seen=set();provenance=[]
    for i in order:
        row=candidates[i];unowned=replace(Individual.restore(row['payload']),case_owner=None).validate();key=program_key(unowned)
        if key in seen:continue
        seen.add(key);chosen.append((row,unowned))
        provenance.append(dict(serial=row['serial'],case_owner=row['case_owner'],source_generation=row['source_generation'],first_seen_generation=row['first_seen_generation'],
            source_seal_sha256=seals[row['source_generation']],case_score=max(row['case_scores']),owned_program_sha256=program_key(Individual.restore(row['payload'])),promoted_program_sha256=key))
        if len(chosen)==target:break
    if not chosen:raise ValueError('No positive verified phase0 specialists; refusing a fresh phase1 campaign')
    pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
    members=[m for _,m in chosen];ids=[r['serial'] for r,_ in chosen]
    for row,promoted in chosen:
        record=dict(row['pedigree'],phase0_program_sha256=row['pedigree']['program_sha256'],program_sha256=program_key(promoted),phase0_case_owner=row['case_owner'],phase0_source_generation=row['source_generation'],case_owner=None)
        record['label']=record['label'].rsplit('-',1)[0]+'-'+record['program_sha256'][:4]
        pedigree.records[int(row['serial'])]=record
    fresh_count=target-len(members)
    for _ in range(fresh_count):
        for attempt in range(64):
            member=sample(rng,1)[0];key=program_key(member)
            if key not in seen:break
        else:raise ValueError('Unique promotion exploration exhausted')
        seen.add(key);members.append(member);ids.extend(pedigree.birth([member],1,[(-1,-1)],['fresh-promotion-exploration']).tolist())
    pedigree.retain(ids)
    result=dict(source_checkpoint=str(cp),source_checkpoint_sha256=file_hash(cp),source_generations=expected_generations,
        population=[m.payload() for m in members],population_sha256=digest([m.payload() for m in members]),ids=ids,
        pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,
        specialist_count=len(chosen),fresh_count=fresh_count,provenance=provenance,
        financial_results_reused=False,validation_opened=False,source_seals=seals)
    if output.exists():
        prior=json.loads(output.read_bytes())
        if prior!=json.loads(json.dumps(result)):raise ValueError('Existing promotion differs')
    else:write_json(output,result)
    return result


def execute(config,day,root,owner):
    from .local_runner import run_session
    root=Path(root)
    phase0_generations=config.get('phase0_generations',30);phase1_generations=config.get('phase1_generations',20)
    for phase,generations in ((0,phase0_generations),(1,phase1_generations)):
        if (root/'STOP').exists():return dict(status='interrupted',phase=phase)
        output=root/f'phase-{phase}'
        write_json(output/'manifest.json',dict(days=[day],local_generations=generations,phase=phase,parent_campaign=str(root),source_sha256=owner['source_sha256']))
        active=dict(owner,campaign=str(output),phase=phase,generations=generations,total_generations=phase0_generations+phase1_generations)
        write_json(root/'active.json',active);write_json(root.parent/'active-test.json',active)
        cfg=dict(config,phase=phase)
        if phase==1:
            promotion=root/'phase1-promotion.json'
            promote(root/'phase-0/local'/day,promotion,expected_generations=phase0_generations)
            cfg['promotion']=str(promotion)
        result=run_session(cfg,day,output/'local'/day,generations,1024,2020,initial_multiplier=6 if phase==0 else 1)
        if result['status']!='complete':return dict(result,phase=phase)
    write_json(root/'complete.json',dict(status='complete',phase0_generations=phase0_generations,phase1_generations=phase1_generations,promotion_sha256=file_hash(root/'phase1-promotion.json'),validation_opened=False))
    return dict(status='complete',phase0_generations=phase0_generations,phase1_generations=phase1_generations)
