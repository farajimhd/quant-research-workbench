"""V53-derived evolution from authorized founder receipts under V59 selection."""
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
    from .entry_sharing import VERSION as entry_sharing_version
    contract=dict(comparison=job.get('comparison'),experiment=dict(mutation_probe_size=job.get('mutation_probe_size',0),crossover_policy='v59-strategy-routing-veto-block-and-existing-v1'),seed=job['seed'],source_sha256=job['source_sha256'],objective=job['objective'],training_sha256=job['training_sha256'],validation_sha256=job['validation_sha256'],generations=job['phase2_generations'],population=job['population'],initial_multiplier=job['initial_multiplier'],training_days=train,validation_days=valid,validation='display-only; successor and RNG frozen before opening')
    if job.get('continuation'):contract['continuation_sha256']=job['continuation']['context_sha256']
    contract['selection_risk_caps']=False
    contract['execution']=job.get('execution',{})
    if job.get('execution_restart'):
        contract['execution_restart']=job['execution_restart']
        contract['execution_tail']=job['execution_tail']
    contract['ticker_parent_fraction']=job.get('ticker_parent_fraction',.25)
    contract['node_budget']=dict(total=1024,per_action=1024,depth=512)
    contract['founder_reuse']=job.get('founder_reuse')
    contract['batch_size']=job.get('batch_size',512)
    contract['entry_sharing']=entry_sharing_version
    if job.get('campaign_stage'):
        contract['campaign_context']={k:job.get(k) for k in ('campaign_stage','parent_job_sha256','initialization_probe_sha256','promotion_exports')}
    if job.get('immigration_policy'):contract['immigration_policy']=job['immigration_policy']
    if job.get('selection',{}).get('profit_fresh_fraction'):contract['profit_fresh_fraction']=job['selection']['profit_fresh_fraction']
    if job.get('score_version'):contract['score_version']=job['score_version']
    contract['concentration_coefficient']=job['concentration_coefficient']
    contract['fresh_founders']=True
    contract['profit_targets']={k:job[k] for k in ('ticker_target','session_target')}
    if job.get('validation_schedule')=='concurrent-training-only-v1':contract['validation']='display-only; isolated training-only reproduction concurrent with replay'
    return contract


class Controller:
    def publish(self,**extra):raise NotImplementedError
    def evaluate(self,role,days,members,generation):raise NotImplementedError

    def reproduce(self,root,folder,members,source_ids,birth_records,token,ordered,training_receipts,train,objective,generation,rng,pedigree,species,probe):
        """Training-only CPU lane: validation values and worker handles are absent."""
        started=time.perf_counter()
        emit=getattr(self,'artifact_write',write_json)
        session_panels=[];panel=aggregate(ordered,objective,session_sink=session_panels)
        from .execution_restart import initial_replay
        if generation==0 and initial_replay(self.job):
            if len(members)!=6144 or len(set(map(int,source_ids)))!=6144 or not panel['eligible'].all():
                raise ValueError('Initial replay must score all 6144 founders without execution exclusions')
            emit(folder/'initial-admission.json',dict(replayed=6144,eligible=6144,execution_excluded=0))
        from .offspring_audit import summarize
        audit=summarize(birth_records,panel,generation)
        self.publish(reproduction_stage='Score training population')
        emit(folder/'training.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(panel),ranking=ranking(panel,source_ids),receipts=training_receipts))
        self.publish(reproduction_stage='Select active parents and reproduce')
        if generation<self.job['phase2_generations']:
            from .validation_cache import validation_scope
            # Retain parents plus offspring for this bounded transaction;
            # 4096 entries thrash even on the 5120 parental action graphs.
            with validation_scope(capacity=min(32768,max(4096,len(members)*15))):
                successor,newids,diversity=next_population(rng,members,source_ids,pedigree,[],generation+1,Selection(**self.job['selection']),session_values=panel['score'][None],session_eligible=panel['eligible'][None],global_values=panel['score'],global_eligible=panel['eligible'],target_size=self.job['population'],species=species,ordered=ordered,case_values=None,case_ids=None,probe=probe,fresh_prefetch=getattr(self,'fresh_prefetch',None))
                if getattr(self,'publication_lane',None):
                    from .validation_cache import certificate
                    from .lineage import program_key
                    self.successor_validated=certificate(successor)
                    self.successor_keys=[program_key(m) for m in successor]
        else:successor,newids,diversity=members,source_ids,dict(reproduction_performed=False)
        if generation<self.job['phase2_generations']:
            selected=set(map(int,source_ids)) & set(map(int,newids))
            audit['retained_ids']=sorted(selected);audit['selection_observed']=True
        emit(folder/'offspring-audit.json',audit)
        if getattr(self,'publication_lane',None):
            from .population_snapshot import Snapshot
            self.successor_snapshot=Snapshot(successor)
            self.frozen_raw,frozen_hash=self.successor_snapshot.successor(newids,rng.bit_generator.state)
            if generation<self.job['phase2_generations'] and hasattr(self,'prepare_successor'):self.prepare_successor(self.successor_snapshot)
        else:
            frozen=dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state)
            frozen_hash=digest(frozen);emit(folder/'successor.json',dict(**frozen,sha256=frozen_hash,training_only=True))
        emit(folder/'reproduction.json',dict(generation=generation,successor_sha256=frozen_hash,
            training_only=True,telemetry=diversity))
        born=[pedigree.records[int(i)] for i in newids if pedigree.records[int(i)]['generation']==generation+1]
        emit(folder/'births.json',born)
        from .evolution_report import publish as report_evolution
        per_session={day:serializable(metrics) for day,metrics in zip(train,session_panels)}
        report=(source_ids,panel,birth_records,set(map(int,newids)) if generation<self.job['phase2_generations'] else None,diversity,generation,per_session)
        if getattr(self,'publication_lane',None):self.pending_report=report
        else:report_evolution(root,folder,*report)
        self.publish(reproduction=diversity,reproduction_stage='Complete',reproduction_seconds=time.perf_counter()-started,reproduction_finished_epoch=time.time())
        return panel,successor,newids,diversity,frozen_hash,time.perf_counter()-started

    def global_run(self,exports,train,valid):
        from .full_seed import global_seed
        from .calibration import load_probe
        probe=load_probe(self.job['initialization_probe'],self.job['training'])
        self.stage='Load sealed checkpoint' if (self.root/'global/checkpoint.json').exists() else 'Blind multi-round reproduction before replay' if self.job.get('campaign_stage')=='global' else 'Initialize session cohort'
        self.publish(phase=2,generation=0,completed_generations=0,completed_sessions=0,total_sessions=len(train))
        root=self.root/'global';root.mkdir(exist_ok=True);objective=Objective(**self.job['objective'])
        contract=checkpoint_contract(self.job,train,valid)
        cp=root/'checkpoint.json';species=Species();completed=0;seals={}
        if cp.exists():
            saved=getattr(self,'resume_checkpoint',None)
            if saved is None:saved=read(cp)
            from .resume_authority import accept_checkpoint
            accept_checkpoint(self.job,cp,saved,contract)
            if digest(saved['state'])!=saved['state_sha256']:raise ValueError('Global resume state changed')
            state=saved['state'];completed=state['completed'];seals=state['seals'];species=Species(state['species'])
            self.publish(generation=max(0,completed),completed_generations=max(0,completed))
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
            if self.job.get('execution_restart'):
                from .execution_restart import seed
                state=seed(self.job)
            else:
                state=global_seed(exports,self.job,probe,**options)
                fresh=self.job.get('fresh_initial_replay')=='crossing-orders-v3'
                state.update(completed=-1 if fresh else 0,seals={},species=species.state)
                if fresh:state['pedigree']=[dict(r,generation=0) for r in state['pedigree']]
            write_json(root/'initial-pool.json',state);write_checkpoint(cp,contract,state)
            completed=state['completed']
        members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids']);pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
        if getattr(self,'pipeline_enabled',False):
            from .generation_flow import evolve
            return evolve(self,root,train,valid,contract,state,members,ids,pedigree,rng,species,probe,objective)
        for generation in range(completed+1,self.job['phase2_generations']+1):
            started=time.perf_counter();folder=root/f'generation-{generation:04d}';folder.mkdir(exist_ok=True);self.stage='Session training' if self.job.get('campaign_stage')=='session' else 'Global training'
            source_population=[m.payload() for m in members];source_ids=ids.copy();token=digest(source_population)
            preparer=getattr(getattr(self,'worker',None),'population_preparer',None)
            if preparer is not None and preparer.token!=token:preparer.submit_members(members,token)
            write_json(folder/'population.json',dict(population=source_population,ids=ids.tolist(),population_sha256=token))
            for serial,member in zip(source_ids,members):pedigree.records[int(serial)]['node_count']=sum(len(n.nodes) for n in member.networks.values())
            birth_records=[pedigree.records[int(i)] for i in source_ids]
            # Identity/ancestry are known before replay. Publish them now so
            # completed session receipts can be explored before the global seal.
            write_json(folder/'evaluated-pedigree.json',birth_records)
            from .allocation import budgets
            from .reproduction_prefetch import FreshPrefetch
            count=budgets(self.job['population'])['fresh'];count-=int(count*self.job['selection'].get('profit_fresh_fraction',0.))
            self.fresh_prefetch=FreshPrefetch(rng,generation+1,count,probe) if generation<self.job['phase2_generations'] else None
            if not valid and self.job.get('mutation_probe_size',0):
                from .mutation_probe import prepare_probe
                self.paired_probe=prepare_probe(self,members,source_ids,birth_records,generation)
            try:
                ordered,training_receipts=self.evaluate('training',train,members,generation)
            except BaseException:
                if self.fresh_prefetch:self.fresh_prefetch.close();self.fresh_prefetch=None
                raise
            from .generation_pipeline import overlap
            self.stage='Phase 2 validation: display only' if valid else 'Training-only reproduction'
            self.publish(reproduction_stage='Queued')
            work=lambda:self.reproduce(root,folder,members,source_ids,birth_records,token,ordered,training_receipts,train,objective,generation,rng,pedigree,species,probe)
            concurrent=bool(valid and self.job.get('validation_schedule')=='concurrent-training-only-v1')
            probe_concurrent=bool(not valid and self.job.get('mutation_probe_size',0))
            self.publish(validation_overlap=concurrent,transition_started_epoch=time.time())
            if probe_concurrent:
                from .mutation_probe import run_probe
                diagnostic_panel=aggregate(ordered,objective)
                self.stage='Reproduction + paired mutation diagnostic';self.publish()
                result,validation_result,timing=overlap(work,lambda:run_probe(self,folder,members,source_ids,birth_records,diagnostic_panel,generation),True)
                timing['mutation_probe_concurrent']=True
            else:
                result,validation_result,timing=overlap(work,lambda:self.evaluate('validation',valid,members,generation),concurrent, bool(valid))
            if self.fresh_prefetch:self.fresh_prefetch.close();self.fresh_prefetch=None
            panel,successor,newids,diversity,frozen_hash,reproduction_seconds=result
            validation,validation_receipts=validation_result if valid else (None,{})
            validation_panel=aggregate(validation,objective) if valid else None
            timing['reproduction_seconds']=reproduction_seconds
            write_json(folder/'transition-timings.json',timing)
            if self.job.get('mutation_probe_size',0) and not probe_concurrent:
                from .mutation_probe import run_probe
                self.stage='Paired mutation diagnostic; selection and RNG isolated';self.publish()
                run_probe(self,folder,members,source_ids,birth_records,panel,generation)
            # Successor is frozen and diagnostics no longer own the preparation
            # slot. Pack next generation on one CPU lane during seal publication.
            preparer=getattr(getattr(self,'worker',None),'population_preparer',None)
            if preparer is not None and generation<self.job['phase2_generations']:
                preparer.submit_members(successor,digest([m.payload() for m in successor]))
            if digest(dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state))!=frozen_hash:raise ValueError('Validation changed successor or training RNG')
            write_json(folder/'validation.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(validation_panel) if valid else {},ranking=ranking(validation_panel,source_ids) if valid else [],opened=bool(valid),receipts=validation_receipts,display_only=True,successor_sha256_before=frozen_hash,successor_sha256_after=frozen_hash))
            observational={name:sha(folder/name) for name in ('evolution-report.json','founder-results.json','session-metrics.json','transition-timings.json') if (folder/name).exists()}
            write_json(folder/'complete.json',dict(observational_reports=observational,training_sha256=sha(folder/'training.json'),validation_sha256=sha(folder/'validation.json'),successor_sha256=sha(folder/'successor.json'),population_sha256=sha(folder/'population.json'),reproduction_sha256=sha(folder/'reproduction.json'),births_sha256=sha(folder/'births.json'),evaluated_pedigree_sha256=sha(folder/'evaluated-pedigree.json'),offspring_audit_sha256=sha(folder/'offspring-audit.json'),mutation_probe_sha256=sha(folder/'mutation-probe.json') if self.job.get('mutation_probe_size',0) else None,source_sha256=self.job['source_sha256'],validation_independent=True,wall_seconds=time.perf_counter()-started))
            seals[str(generation)]=sha(folder/'complete.json');members=successor;ids=newids;pedigree.retain(ids)
            state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,seals=seals,species=species.state)
            write_checkpoint(cp,contract,state)
            from .evolution_report import expose
            expose(root,folder)
            write_json(folder/'generation-timings.json',dict(generation=generation,seal_sha256=seals[str(generation)],
                full_generation_seconds=time.perf_counter()-started,includes_checkpoint_and_observer_publication=True))
            self.publish(phase=2,generation=generation,ranking_generation=generation,completed_generations=generation,training_ranking=ranking(panel,source_ids),validation_ranking=ranking(validation_panel,source_ids) if valid else [],validation_display_only=True)
            # Finished episode ledgers have sealed receipts. Do not retain the
            # previous generation's training/validation lists during new replay.
            del ordered,validation,validation_result,training_receipts,validation_receipts,work,result
        write_json(root/'complete.json',dict(status='complete',checkpoint_sha256=sha(cp),completed=self.job['phase2_generations'],training_sessions=len(train),validation_sessions=len(valid),validation_independent=True))

    def run(self):
        raise RuntimeError('Use the V59 pilot entry; local phases and all-session launch are disabled')
