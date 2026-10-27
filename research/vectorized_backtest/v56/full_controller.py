"""V53-derived evolution from authorized founder receipts under V56 selection."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,traceback
from pathlib import Path
import numpy as np
import psutil
from .full_common import check_job,aggregate,ranking,serializable
from .objectives import Objective
from .identity import digest
from .genome import Individual
from .local_runner import CompactPedigree
from .lineage import next_population,Selection
from .species import Species
from research.vectorized_backtest.v42.distributed_bus import Bus,read
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


def checkpoint_contract(job,train,valid):
    contract=dict(comparison=job.get('comparison'),experiment=dict(mutation_probe_size=job.get('mutation_probe_size',0),crossover_policy='action-composition-or-and-xor-xnor-v1'),seed=job['seed'],source_sha256=job['source_sha256'],objective=job['objective'],training_sha256=job['training_sha256'],validation_sha256=job['validation_sha256'],generations=job['phase2_generations'],population=job['population'],initial_multiplier=job['initial_multiplier'],training_days=train,validation_days=valid,validation='display-only; successor and RNG frozen before opening')
    if job.get('continuation'):contract['continuation_sha256']=job['continuation']['context_sha256']
    contract['selection_risk_caps']=False
    contract['execution']=job.get('execution',{})
    contract['ticker_parent_fraction']=job.get('ticker_parent_fraction',.25)
    contract['node_budget']=dict(total=1024,per_action=1024,depth=512)
    contract['founder_reuse']=job.get('founder_reuse')
    contract['batch_size']=job.get('batch_size',512)
    if job.get('campaign_stage'):
        contract['campaign_context']={k:job.get(k) for k in ('campaign_stage','parent_job_sha256','initialization_probe_sha256','promotion_exports')}
    return contract


class Controller:
    def publish(self,**extra):raise NotImplementedError
    def evaluate(self,role,days,members,generation):raise NotImplementedError

    def global_run(self,exports,train,valid):
        from .full_seed import global_seed
        from research.vectorized_backtest.v42.calibration import load_probe
        probe=load_probe(self.job['initialization_probe'],self.job['training'])
        self.stage='Load sealed checkpoint' if (self.root/'global/checkpoint.json').exists() else 'Blind multi-round reproduction before replay' if self.job.get('campaign_stage')=='global' else 'Initialize session cohort'
        self.publish(phase=2,generation=0,completed_generations=0,completed_sessions=0,total_sessions=len(train))
        root=self.root/'global';root.mkdir(exist_ok=True);objective=Objective(**self.job['objective'])
        contract=checkpoint_contract(self.job,train,valid)
        cp=root/'checkpoint.json';species=Species();completed=0;seals={}
        if cp.exists():
            saved=read(cp)
            from .resume_authority import accept_checkpoint
            accept_checkpoint(self.job,cp,saved,contract)
            if digest(saved['state'])!=saved['state_sha256']:raise ValueError('Global resume state changed')
            state=saved['state'];completed=state['completed'];seals=state['seals'];species=Species(state['species'])
            self.publish(generation=completed,completed_generations=completed)
            for g,h in seals.items():
                f=root/f'generation-{int(g):04d}'
                if sha(f/'complete.json')!=h:raise ValueError('Global checkpoint seal link changed')
                seal=read(f/'complete.json')
                for filename,key in (('training.json','training_sha256'),('validation.json','validation_sha256'),('successor.json','successor_sha256'),('population.json','population_sha256')):
                    if sha(f/filename)!=seal[key]:raise ValueError('Sealed global evidence changed')
                if 'reproduction_sha256' in seal and sha(f/'reproduction.json')!=seal['reproduction_sha256']:
                    raise ValueError('Sealed reproduction evidence changed')
                for filename,expected in seal.get('observational_reports',{}).items():
                    if sha(f/filename)!=expected:raise ValueError('Sealed evolution report changed')
                for filename,key in (('births.json','births_sha256'),('evaluated-pedigree.json','evaluated_pedigree_sha256'),('offspring-audit.json','offspring_audit_sha256')):
                    if sha(f/filename)!=seal[key]:raise ValueError('Sealed offspring evidence changed')
                if self.job.get('mutation_probe_size',0) and sha(f/'mutation-probe.json')!=seal['mutation_probe_sha256']:raise ValueError('Mutation diagnostic evidence changed')
                validation=read(f/'validation.json')
                if validation['successor_sha256_before']!=validation['successor_sha256_after'] or not validation['display_only']:raise ValueError('Validation independence proof changed')
        else:
            options=dict(progress=lambda done,total:self.publish(initialization_progress=dict(completed=done,total=total))) if self.job.get('campaign_stage')=='global' else {}
            state=global_seed(exports,self.job,probe,**options);state.update(completed=0,seals={},species=species.state)
            write_json(root/'initial-pool.json',state);write_checkpoint(cp,contract,state)
        members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids']);pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
        for generation in range(completed+1,self.job['phase2_generations']+1):
            started=time.perf_counter();folder=root/f'generation-{generation:04d}';folder.mkdir(exist_ok=True);self.stage='Session training' if self.job.get('campaign_stage')=='session' else 'Global training'
            source_population=[m.payload() for m in members];source_ids=ids.copy();token=digest(source_population)
            write_json(folder/'population.json',dict(population=source_population,ids=ids.tolist(),population_sha256=token))
            ordered,training_receipts=self.evaluate('training',train,members,generation);session_panels=[];panel=aggregate(ordered,objective,session_sink=session_panels)
            for serial,member in zip(source_ids,members):pedigree.records[int(serial)]['node_count']=sum(len(n.nodes) for n in member.networks.values())
            birth_records=[pedigree.records[int(i)] for i in source_ids]
            write_json(folder/'evaluated-pedigree.json',birth_records)
            from .offspring_audit import summarize
            write_json(folder/'offspring-audit.json',summarize(birth_records,panel,generation))
            self.stage='Score evaluated population';self.publish()
            if self.job.get('mutation_probe_size',0):
                from .mutation_probe import run_probe
                self.stage='Paired mutation diagnostic; selection and RNG isolated';self.publish()
                run_probe(self,folder,members,source_ids,birth_records,panel,generation)
            write_json(folder/'training.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(panel),ranking=ranking(panel,source_ids),receipts=training_receipts))
            self.stage='Select active parents and reproduce';self.publish()
            if generation<self.job['phase2_generations']:
                from .validation_cache import validation_scope
                # Retain parents plus offspring for this bounded transaction;
                # 4096 entries thrash even on the 5120 parental action graphs.
                with validation_scope(capacity=min(32768,max(4096,len(members)*15))):
                    successor,newids,diversity=next_population(rng,members,ids,pedigree,[],generation+1,Selection(**self.job['selection']),session_values=panel['score'][None],session_eligible=panel['eligible'][None],global_values=panel['score'],global_eligible=panel['eligible'],target_size=self.job['population'],species=species,ordered=ordered,case_values=None,case_ids=None,probe=probe)
            else:successor,newids,diversity=members,ids,dict(reproduction_performed=False)
            if generation<self.job['phase2_generations']:
                audit=read(folder/'offspring-audit.json');selected=set(map(int,source_ids)) & set(map(int,newids))
                audit['retained_ids']=sorted(selected);audit['selection_observed']=True
                write_json(folder/'offspring-audit.json',audit)
            frozen=dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state)
            frozen_hash=digest(frozen);write_json(folder/'successor.json',dict(**frozen,sha256=frozen_hash,training_only=True))
            write_json(folder/'reproduction.json',dict(generation=generation,successor_sha256=frozen_hash,
                training_only=True,telemetry=diversity))
            born=[pedigree.records[int(i)] for i in newids if pedigree.records[int(i)]['generation']==generation+1]
            write_json(folder/'births.json',born)
            from .evolution_report import publish as report_evolution
            per_session={day:serializable(metrics) for day,metrics in zip(train,session_panels)}
            report_evolution(root,folder,source_ids,panel,birth_records,set(map(int,newids)) if generation<self.job['phase2_generations'] else None,diversity,generation,per_session)
            self.publish(reproduction=diversity)
            self.stage='Phase 2 validation: display only' if valid else 'Seal training generation; validation unopened'
            if valid:
                validation,validation_receipts=self.evaluate('validation',valid,members,generation);validation_panel=aggregate(validation,objective)
            else:validation_panel=None;validation_receipts={}
            if digest(dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state))!=frozen_hash:raise ValueError('Validation changed successor or training RNG')
            write_json(folder/'validation.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(validation_panel) if valid else {},ranking=ranking(validation_panel,source_ids) if valid else [],opened=bool(valid),receipts=validation_receipts,display_only=True,successor_sha256_before=frozen_hash,successor_sha256_after=frozen_hash))
            observational={name:sha(folder/name) for name in ('evolution-report.json','founder-results.json','session-metrics.json') if (folder/name).exists()}
            write_json(folder/'complete.json',dict(observational_reports=observational,training_sha256=sha(folder/'training.json'),validation_sha256=sha(folder/'validation.json'),successor_sha256=sha(folder/'successor.json'),population_sha256=sha(folder/'population.json'),reproduction_sha256=sha(folder/'reproduction.json'),births_sha256=sha(folder/'births.json'),evaluated_pedigree_sha256=sha(folder/'evaluated-pedigree.json'),offspring_audit_sha256=sha(folder/'offspring-audit.json'),mutation_probe_sha256=sha(folder/'mutation-probe.json') if self.job.get('mutation_probe_size',0) else None,source_sha256=self.job['source_sha256'],validation_independent=True,wall_seconds=time.perf_counter()-started))
            seals[str(generation)]=sha(folder/'complete.json');members=successor;ids=newids;pedigree.retain(ids)
            state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,seals=seals,species=species.state)
            write_checkpoint(cp,contract,state)
            from .evolution_report import expose
            expose(root,folder)
            self.publish(phase=2,generation=generation,ranking_generation=generation,completed_generations=generation,training_ranking=ranking(panel,source_ids),validation_ranking=ranking(validation_panel,source_ids) if valid else [],validation_display_only=True)
        write_json(root/'complete.json',dict(status='complete',checkpoint_sha256=sha(cp),completed=self.job['phase2_generations'],training_sessions=len(train),validation_sessions=len(valid),validation_independent=True))

    def run(self):
        raise RuntimeError('Use the V56 pilot entry; local phases and all-session launch are disabled')
