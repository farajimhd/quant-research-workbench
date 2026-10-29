"""Selection/packing critical path with one ordered, bounded publication process."""
import copy,time
from .population_snapshot import Snapshot
from .publication_lane import Lane,Buffer,start_generation,finish_generation
from .reproduction_prefetch import FreshPrefetch
from .allocation import budgets
from .mutation_probe import prepare_probe,run_probe
from .full_common import aggregate,ranking,serializable
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_assets import sha


def evolve(c,root,train,valid,contract,state,members,ids,pedigree,rng,species,probe,objective):
    if valid and c.job.get('version')!='v57-global-v1':raise ValueError('The direct pilot cannot open validation sessions')
    lane=Lane();c.publication_lane=lane;c.worker.defer_publication=True
    snapshot=Snapshot(members);last=None;completed=state['completed'];c.sealed_generation=completed
    try:
        for generation in range(completed+1,c.job['phase2_generations']+1):
            started=time.perf_counter();lane.check()
            if last is not None and last.done():c.sealed_generation=last.result()['generation']
            folder=root/f'generation-{generation:04d}';folder.mkdir(exist_ok=True)
            c.stage='Global training';c.cohort_snapshot=snapshot;snapshot.verify(members)
            ids=ids.copy();token=snapshot.token
            for serial,member in zip(ids,members):pedigree.records[int(serial)]['node_count']=sum(len(n.nodes) for n in member.networks.values())
            records=copy.deepcopy([pedigree.records[int(i)] for i in ids])
            lane.submit(start_generation,str(folder),snapshot.raw,token,ids.tolist(),records)
            preparer=getattr(c.worker,'population_preparer',None)
            if preparer is not None:preparer.submit_snapshot(snapshot)
            c.published_receipts=[]
            count=budgets(c.job['population'])['fresh'];count-=int(count*c.job['selection'].get('profit_fresh_fraction',0.))
            c.fresh_prefetch=FreshPrefetch(rng,generation+1,count,probe) if generation<c.job['phase2_generations'] else None
            if c.fresh_prefetch:
                from .cpu_work import descriptors
                species.descriptor_future=c.fresh_prefetch.pool.submit(descriptors,members)
            c.paired_probe=prepare_probe(c,members,ids,records,generation)
            buffer=Buffer();c.artifact_write=buffer.write
            replay_started=time.perf_counter()
            ordered,receipts=c.evaluate('training',train,members,generation)
            replay_returned=time.perf_counter()
            # A failed publication is surfaced before producing another cohort.
            lane.check()
            c.stage='Reproduction';c.publish(reproduction_stage='Select active parents and reproduce')
            from .generation_pipeline import overlap
            result,validation_result,overlap_timing=overlap(
                lambda:c.reproduce(root,folder,members,ids,records,token,ordered,receipts,train,objective,generation,rng,pedigree,species,probe),
                lambda:c.evaluate('validation',valid,members,generation),bool(valid),bool(valid))
            panel,successor,newids,diversity,frozen_hash,reproduction_seconds=result
            next_snapshot=c.successor_snapshot
            if generation<c.job['phase2_generations'] and preparer is not None:
                preparer.submit_snapshot(next_snapshot,c.successor_keys,c.successor_validated)
            # Diagnostics consume already computed metrics, never another replay.
            run_probe(c,folder,members,ids,records,panel,generation)
            if c.fresh_prefetch:c.fresh_prefetch.close();c.fresh_prefetch=None
            snapshot.verify(members);next_snapshot.verify(successor)
            frozen_raw,verified_hash=next_snapshot.successor(newids,rng.bit_generator.state)
            if frozen_hash!=verified_hash:raise ValueError('Diagnostic changed successor or training RNG')
            timing=dict(concurrent=True,publication_process=True,reproduction_seconds=reproduction_seconds,
                        total_seconds=time.perf_counter()-replay_returned,replay_dispatch_seconds=replay_started-started,
                        replay_return_seconds=replay_returned-started)
            timing['validation_overlap']=overlap_timing
            buffer.write(folder/'transition-timings.json',timing)
            validation=dict(population_sha256=token,ids=ids.tolist(),panel={},ranking=[],opened=False,receipts={},display_only=True,
                            successor_sha256_before=frozen_hash,successor_sha256_after=frozen_hash)
            if valid:
                validation_panel=aggregate(validation_result[0],objective)
                validation.update(panel=serializable(validation_panel),ranking=ranking(validation_panel,ids),opened=True,receipts=validation_result[1])
            pedigree.retain(newids)
            next_state=dict(ids=newids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,
                            rng=copy.deepcopy(rng.bit_generator.state),species=copy.deepcopy(species.state))
            packet=dict(root=str(root),folder=str(folder),generation=generation,contract=contract,state=next_state,
                        source=c.job['source_sha256'],started=started,critical_path_seconds=time.perf_counter()-started,
                        successor_raw=next_snapshot.raw,frozen_raw=frozen_raw,frozen_hash=frozen_hash,
                        replay_writes=c.worker.deferred_writes,artifacts=buffer.writes,report=c.pending_report,validation=validation,
                        published_receipts=list(c.published_receipts))
            # Spawn feeder serializes asynchronously: detach every mutable record.
            packet['state']['pedigree']=copy.deepcopy(packet['state']['pedigree'])
            last=lane.submit(finish_generation,packet)
            c.publication_future=last
            c.publish(phase=2,generation=generation,ranking_generation=generation,completed_generations=c.sealed_generation,
                      training_ranking=ranking(panel,ids),validation_ranking=validation['ranking'],validation_display_only=True,
                      publication_pending_generation=generation,reproduction_stage='Complete; publishing asynchronously')
            members,ids,snapshot=successor,newids,next_snapshot
            del packet,ordered,result,receipts
        last.result();c.sealed_generation=c.job['phase2_generations']
        c.publish(completed_generations=c.sealed_generation,publication_pending_generation=None)
        write_json(root/'complete.json',dict(status='complete',checkpoint_sha256=sha(root/'checkpoint.json'),completed=c.sealed_generation,
                    training_sessions=len(train),validation_sessions=len(valid),validation_independent=True))
    finally:
        try:lane.close()
        finally:
            c.worker.defer_publication=False;c.publication_lane=None
            if c.fresh_prefetch:c.fresh_prefetch.close();c.fresh_prefetch=None
            c.artifact_write=write_json
