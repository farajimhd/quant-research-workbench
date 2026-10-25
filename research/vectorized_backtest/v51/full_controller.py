"""Source-bound V49 phase2 evolution, with fresh V51 founders and revised cost."""
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


class Controller:
    def publish(self,**extra):raise NotImplementedError
    def evaluate(self,role,days,members,generation):raise NotImplementedError

    def global_run(self,exports,train,valid):
        from .full_seed import global_seed
        from research.vectorized_backtest.v42.calibration import load_probe
        probe=load_probe(self.job['initialization_probe'],self.job['training'])
        self.stage='Load sealed global checkpoint' if (self.root/'global/checkpoint.json').exists() else 'Initialize fresh random/template phase2 cohort'
        self.publish(phase=2,generation=0,completed_generations=0,completed_sessions=0,total_sessions=len(train))
        root=self.root/'global';root.mkdir(exist_ok=True);objective=Objective(**self.job['objective'])
        contract=dict(source_sha256=self.job['source_sha256'],objective=self.job['objective'],training_sha256=self.job['training_sha256'],validation_sha256=self.job['validation_sha256'],generations=self.job['phase2_generations'],population=self.job['population'],initial_multiplier=self.job['initial_multiplier'],training_days=train,validation_days=valid,validation='display-only; successor and RNG frozen before opening')
        if 'initial_reuse_sha256' in self.job:
            contract['initial_reuse_sha256']=self.job['initial_reuse_sha256']
        cp=root/'checkpoint.json';species=Species();completed=0;seals={}
        if cp.exists():
            saved=read(cp)
            if saved['contract']!=contract or digest(saved['state'])!=saved['state_sha256']:raise ValueError('Global resume contract changed')
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
                validation=read(f/'validation.json')
                if validation['successor_sha256_before']!=validation['successor_sha256_after'] or not validation['display_only']:raise ValueError('Validation independence proof changed')
            if completed:
                last=root/f'generation-{completed:04d}'
                self.publish(ranking_generation=completed,training_ranking=read(last/'training.json')['ranking'],
                    validation_ranking=read(last/'validation.json')['ranking'],validation_display_only=True)
        else:
            state=global_seed(exports,self.job,probe);state.update(completed=0,seals={},species=species.state)
            write_json(root/'initial-pool.json',state);write_checkpoint(cp,contract,state)
        members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids']);pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
        for generation in range(completed+1,self.job['phase2_generations']+1):
            started=time.perf_counter();folder=root/f'generation-{generation:04d}';folder.mkdir(exist_ok=True);self.stage='Phase 2 training'
            source_population=[m.payload() for m in members];source_ids=ids.copy();token=digest(source_population)
            write_json(folder/'population.json',dict(population=source_population,ids=ids.tolist(),population_sha256=token))
            ordered,training_receipts=self.evaluate('training',train,members,generation);panel=aggregate(ordered,objective)
            write_json(folder/'training.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(panel),ranking=ranking(panel,source_ids),receipts=training_receipts))
            if generation<self.job['phase2_generations']:
                from .validation_cache import validation_scope
                with validation_scope():
                    successor,newids,diversity=next_population(rng,members,ids,pedigree,[],generation+1,Selection(**self.job['selection']),session_values=panel['score'][None],session_eligible=panel['eligible'][None],global_values=panel['score'],global_eligible=panel['eligible'],target_size=self.job['population'],species=species,ordered=ordered,case_values=None,case_ids=None,probe=probe)
            else:successor,newids,diversity=members,ids,species.telemetry
            frozen=dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state)
            frozen_hash=digest(frozen);write_json(folder/'successor.json',dict(**frozen,sha256=frozen_hash,training_only=True))
            write_json(folder/'reproduction.json',dict(generation=generation,successor_sha256=frozen_hash,
                training_only=True,telemetry=diversity))
            self.publish(reproduction=diversity)
            self.stage='Phase 2 validation: display only' if valid else 'Seal training generation; validation unopened'
            if valid:
                validation,validation_receipts=self.evaluate('validation',valid,members,generation);validation_panel=aggregate(validation,objective)
            else:validation_panel=None;validation_receipts={}
            if digest(dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state))!=frozen_hash:raise ValueError('Validation changed successor or training RNG')
            write_json(folder/'validation.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(validation_panel) if valid else {},ranking=ranking(validation_panel,source_ids) if valid else [],opened=bool(valid),receipts=validation_receipts,display_only=True,successor_sha256_before=frozen_hash,successor_sha256_after=frozen_hash))
            write_json(folder/'complete.json',dict(training_sha256=sha(folder/'training.json'),validation_sha256=sha(folder/'validation.json'),successor_sha256=sha(folder/'successor.json'),population_sha256=sha(folder/'population.json'),reproduction_sha256=sha(folder/'reproduction.json'),source_sha256=self.job['source_sha256'],validation_independent=True,wall_seconds=time.perf_counter()-started))
            seals[str(generation)]=sha(folder/'complete.json');members=successor;ids=newids;pedigree.retain(ids)
            state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,seals=seals,species=species.state)
            write_checkpoint(cp,contract,state)
            self.publish(phase=2,generation=generation,ranking_generation=generation,completed_generations=generation,training_ranking=ranking(panel,source_ids),validation_ranking=ranking(validation_panel,source_ids) if valid else [],validation_display_only=True)
        write_json(root/'complete.json',dict(status='complete',checkpoint_sha256=sha(cp),completed=self.job['phase2_generations'],training_sessions=len(train),validation_sessions=len(valid),validation_independent=True))

    def run(self):
        raise RuntimeError('Use the V51 pilot entry; local phases and all-session launch are disabled')
