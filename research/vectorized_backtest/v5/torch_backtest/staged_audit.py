"""Replay staged selection/RNG and independently audit all bound training fills."""
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .runtime import file_hash,write_json,code_hash
from .run_search import state,restore,fingerprint,clean,metric_summary
from .staged import Stage,validate_schedule,balanced_panels,migrate,objective_matrix
from .staged_search import rank_valid
from .evolution import sample
from .genome import StrategySpace
from .stability import Objective
from .financial_audit import audit_fills
from .batched import merge_metrics


def read(path):return json.loads(Path(path).read_text())


def verified_panel(root,folder_name,record,population,sessions,space,objective):
    expected=fingerprint([state(v) for v in population]);folder=root/folder_name
    if record['population']!=[state(v) for v in population] or record['population_sha256']!=expected:
        raise ValueError('Seed/RNG population lineage mismatch')
    if len(record['receipts'])!=len(sessions):raise ValueError('Incomplete scheduled session panel')
    results=[];bindings=[]
    for index,(item,session) in enumerate(zip(record['receipts'],sessions)):
        path=folder/f'session_{index:03d}'/'receipt.json'
        if Path(item['path']).resolve()!=path.resolve() or file_hash(path)!=item['sha256']:
            raise ValueError('Session receipt path/hash mismatch')
        receipt=read(path)
        if receipt['day']!=session['day'] or receipt['population_sha256']!=expected or receipt.get('profile_seconds') is not None:
            raise ValueError('Session day/population/full-duration mismatch')
        order=receipt.get('candidate_order',list(range(len(population))))
        if sorted(order)!=list(range(len(population))):raise ValueError('Execution permutation loses/duplicates candidates')
        parts=[];left=0
        for index,batch in enumerate(receipt['batch_receipts']):
            directory=f'batch_{index:04d}';batch_path=path.parent/directory/'receipt.json'
            if batch['directory']!=directory or file_hash(batch_path)!=batch['sha256']:
                raise ValueError('Batch order/path/hash mismatch')
            value=read(batch_path);count=value['candidate_count'];indices=order[left:left+count];members=[population[i] for i in indices]
            if (count<1 or value['candidate_start']!=left or len(members)!=count
                    or value.get('candidate_indices',list(range(left,left+count)))!=indices
                    or value['population_sha256']!=fingerprint([state(v) for v in members])):
                raise ValueError('Candidate coverage/identity mismatch')
            source_binding={key:receipt[key] for key in ('day','execution','feature_certificate','prior_certificate','identity_map_sha256','split_certificate_sha256','previous_split_certificate_sha256')}
            if value['session_binding_sha256']!=fingerprint(source_binding):raise ValueError('Batch source binding mismatch')
            if file_hash(batch_path.parent/'fills.pt')!=value['ledger_sha256']:raise ValueError('Actual fill bytes changed')
            audit_fills(batch_path.parent/'fills.pt',value['metrics'],initial_cash=space.settings.initial_cash)
            parts.append(value['metrics']);left+=count
        if left!=len(population) or merge_metrics(parts,order)!=receipt['metrics']:
            raise ValueError('Candidate metrics do not reconcile with batch ledgers')
        results.append(receipt['metrics']);bindings.append(dict(path=str(path),sha256=file_hash(path),day=receipt['day']))
    calculated=objective_matrix(results,population,objective)
    if clean(calculated)!=record['scores']:raise ValueError('Objective or validity arithmetic changed')
    return results,calculated,bindings


def audit(root,*,fresh_inputs=False,freeze=False):
    root=Path(root).resolve()
    if (root/'owner.lock').exists():raise ValueError('Audit requires released worker ownership')
    identity=read(root/'identity.json');spec=identity['sessions']
    if len(spec['training'])!=30 or len(spec['validation'])!=6:raise ValueError('Original30/six split required')
    stages=validate_schedule([Stage(**s) for s in identity['schedule']]);objective=Objective(**identity['objective'])
    space=StrategySpace()
    if identity['financial_settings']!=__import__('dataclasses').asdict(space.settings):raise ValueError('Financial contract changed')
    rng=np.random.default_rng(identity['arguments']['seed']);features=identity['searchable_features']
    # The controller draws the panel schedule before the initial population.
    panels=balanced_panels(rng,stages[0].end_generation,stages[0].sessions,30)
    population=sample(rng,space,stages[0].population,features)
    archive={};generation=0;records=[];all_bindings=[];expected_finalist=None
    for stage_index,stage in enumerate(stages):
        start=0 if stage_index==0 else stages[stage_index-1].end_generation
        while generation<stage.end_generation:
            name=f'generation_{generation:03d}';record=read(root/name/'generation.json')
            selected=[spec['training'][i] for i in panels[generation-start]]
            if record['selected_days']!=[s['day'] for s in selected]:raise ValueError('Seeded training panel changed')
            results,scored,bindings=verified_panel(root,name,record,population,selected,space,objective)
            rank=rank_valid(scored)
            if not rank:raise ValueError('All-invalid generation cannot have evolved')
            top=rank[:stage.archive_top];others=[i for i in rank if i not in top]
            random=rng.choice(others,min(stage.archive_random,len(others)),replace=False).tolist()
            for i in top+random:
                value=state(population[i]);archive[fingerprint(value)]=value
            population=migrate(rng,population,rank,stage.population,space,features)
            records.append(dict(path=str(root/name/'generation.json'),sha256=file_hash(root/name/'generation.json')))
            all_bindings.extend(bindings);generation+=1
        finalists=[restore(value) for _,value in sorted(archive.items())]
        name=f'checkpoint_stage_{stage_index:02d}';record=read(root/name/'ranking.json')
        results,scored,bindings=verified_panel(root,name,record,finalists,spec['training'],space,objective)
        rank=rank_valid(scored)
        if not rank:raise ValueError('No valid full-training finalist')
        records.append(dict(path=str(root/name/'ranking.json'),sha256=file_hash(root/name/'ranking.json')));all_bindings.extend(bindings)
        if stage_index==len(stages)-1:
            expected_finalist=dict(winner=state(finalists[rank[0]]),score=float(scored['score'][rank[0]]),
                                   metrics=metric_summary(results,scored,rank[0],finalists),
                                   identity_sha256=file_hash(root/'identity.json'),ranking_sha256=file_hash(root/name/'ranking.json'))
        else:
            next_stage=stages[stage_index+1]
            population=migrate(rng,finalists,rank,next_stage.population,space,features)
            archive={fingerprint(state(finalists[i])):state(finalists[i]) for i in rank[:next_stage.archive_top]}
            panels=balanced_panels(rng,next_stage.end_generation-generation,next_stage.sessions,30)
    checkpoint=read(root/'checkpoint.json')
    expected_checkpoint=dict(stage_index=len(stages)-1,generation=generation,archive=archive,panels=panels,population=[state(v) for v in population],rng=rng.bit_generator.state)
    if checkpoint!=expected_checkpoint:raise ValueError('Final exact checkpoint/RNG/archive differs from deterministic replay')
    if read(root/'finalist.json')!=clean(expected_finalist):raise ValueError('Selected finalist is not exact final full-training leader')
    report=dict(status='passed',full_budget_verified=True,generations=generation,stages=len(stages),
                identity_sha256=file_hash(root/'identity.json'),checkpoint_sha256=file_hash(root/'checkpoint.json'),
                finalist_sha256=file_hash(root/'finalist.json'),record_bindings=records,session_bindings=all_bindings,
                auditor_code_hash=code_hash(),simulation_code_hash=identity['code_hash'],validation_opened=False)
    write_json(root/'staged_audit.json',report)
    if fresh_inputs or freeze:
        from .input_audit import FreshHashes,verify_session
        hashes=FreshHashes();by_day={s['day']:s for s in spec['training']}
        for item in all_bindings:
            receipt=read(item['path']);verify_session(by_day[receipt['day']],receipt,hashes)
        inputs=dict(status='passed',audit_sha256=file_hash(root/'staged_audit.json'),files=list(hashes.files.values()),validation_opened=False)
        write_json(root/'staged_input_audit.json',inputs)
    if freeze:
        value=dict(expected_finalist,criterion=identity['objective'],audit_sha256=file_hash(root/'staged_audit.json'),
                   input_audit_sha256=file_hash(root/'staged_input_audit.json'),frozen_epoch=time.time())
        path=root/'frozen_winner.json'
        if path.exists():
            previous=read(path)
            if any(previous[key]!=value[key] for key in value if key!='frozen_epoch'):raise ValueError('Immutable frozen winner changed')
        else:write_json(path,clean(value))
    return report


def require_frozen(root):
    """Authorize final producers/evaluator from bound audits without opening data."""
    root=Path(root).resolve();identity=read(root/'identity.json');freeze=read(root/'frozen_winner.json')
    report=read(root/'staged_audit.json');inputs=read(root/'staged_input_audit.json');finalist=read(root/'finalist.json')
    if (freeze['identity_sha256']!=file_hash(root/'identity.json') or report['identity_sha256']!=freeze['identity_sha256']
            or freeze['audit_sha256']!=file_hash(root/'staged_audit.json') or report.get('status')!='passed'
            or not report.get('full_budget_verified') or report['checkpoint_sha256']!=file_hash(root/'checkpoint.json')
            or report['finalist_sha256']!=file_hash(root/'finalist.json') or inputs.get('status')!='passed'
            or freeze['input_audit_sha256']!=file_hash(root/'staged_input_audit.json')
            or inputs['audit_sha256']!=freeze['audit_sha256'] or freeze['criterion']!=identity['objective']
            or any(freeze[key]!=finalist[key] for key in ('winner','score','metrics','ranking_sha256'))):
        raise ValueError('Final access requires exact bound full-budget audit/input audit/freeze')
    for binding in report['record_bindings']:
        path=Path(binding['path']).resolve()
        if not path.is_relative_to(root) or file_hash(path)!=binding['sha256']:raise ValueError('Audited training record changed')
    for item in inputs['files']:
        stamp=Path(item['path']).stat()
        if [stamp.st_size,stamp.st_mtime_ns]!=item['stamp']:raise ValueError('Audited training input changed after freeze')
    return identity,freeze


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--fresh-inputs',action='store_true');p.add_argument('--freeze',action='store_true');a=p.parse_args(argv)
    print(json.dumps(audit(a.output,fresh_inputs=a.fresh_inputs,freeze=a.freeze)));return 0

if __name__=='__main__':raise SystemExit(main())
