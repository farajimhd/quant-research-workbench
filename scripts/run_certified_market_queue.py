"""Run the complete admitted market universe with an explicit incremental budget.

The frozen queue is complete; storage admission covers only bounded
increments, never a fabricated whole-population compression-rate projection.
Existing pilot/tranche datasets remain independent and are not overwritten.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import json
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,timezone
from threading import Event,RLock
from types import SimpleNamespace
from uuid import uuid4,uuid5,NAMESPACE_URL

from scripts import build_market_day as builder
from pipelines.certified_research import contracts,source,universe,cohorts,recipe,independent_market
from pipelines.certified_research import installation,campaign,verification,runtime_plan,acquisition,schema
from pipelines.certified_research.launch import ExecutionLaunch
from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session

GIB=1024**3
ENVELOPE=128*GIB
RESERVE=1024*GIB
GLOBAL_CLAIM=str(uuid5(NAMESPACE_URL,'certified-research/arte/market-full-population-writer-v1'))


class IncrementalBudget:
    """Engineering allowance, rechecked before every guarded producer step.

    One worker can hold at most one listing-day market write. The default
    128 GiB allowance retains the earlier pilot envelope; an explicit 16 to
    128 GiB per-worker allowance is an engineering reservation, never a
    qualified population compression rate.
    All arte parts (including inactive merge copies and concurrent products)
    count conservatively against the campaign's baseline growth budget.
    """
    def __init__(self,query,baseline,budget,stop,*,execution_workers=1,execution_memory_bytes=2*GIB,
                 per_worker_envelope_bytes=ENVELOPE):
        if type(execution_workers) is not int or not 1<=execution_workers<=8:
            raise ValueError('Incremental queue requires between 1 and 8 execution workers')
        if (type(execution_memory_bytes) is not int or not execution_workers*2*GIB<=execution_memory_bytes<=16*GIB):
            raise ValueError('Incremental queue needs 2 GiB per worker and at most 16 GiB')
        if type(per_worker_envelope_bytes) is not int or not 16*GIB<=per_worker_envelope_bytes<=ENVELOPE:
            raise ValueError('Per-worker engineering envelope must be between 16 and 128 GiB')
        self.query=query;self.baseline=baseline;self.budget=budget;self.stop=stop
        self.execution_workers=execution_workers;self.execution_memory_bytes=execution_memory_bytes
        self.per_worker_envelope=per_worker_envelope_bytes
        self.envelope=execution_workers*per_worker_envelope_bytes
        self.lock=RLock();self.reason=None

    def check(self):
        with self.lock:
            disks=self.query("SELECT name,free_space,unreserved_space FROM system.disks WHERE name IN ('live_market_ssd','sip_raw_ssd')")
            used=int(self.query("SELECT coalesce(sum(bytes_on_disk),0) n FROM system.parts WHERE database='arte'")[0]['n'])
            growth=max(0,used-self.baseline)
            reason=None
            if {r['name'] for r in disks}!={'live_market_ssd','sip_raw_ssd'}:reason='required SSD disk missing'
            elif min(min(int(r['free_space']),int(r['unreserved_space'])) for r in disks)<RESERVE+self.envelope:
                reason=f'SSD free space below 1 TiB reserve plus {self.execution_workers} worker increment envelopes'
            elif growth+self.envelope>self.budget:reason='retained growth plus parallel increment envelopes exceeds campaign budget'
            if reason:
                self.reason=reason;self.stop.set();raise RuntimeError('Incremental budget pause: '+reason)
            return dict(mode='incremental-engineering-budget-v1',retained_growth_bytes=growth,
                total_budget_bytes=self.budget,next_increment_envelope_bytes=self.envelope,
                per_worker_envelope_bytes=self.per_worker_envelope,execution_workers=self.execution_workers,
                execution_memory_bytes=self.execution_memory_bytes,
                reserve_bytes=RESERVE,whole_population_capacity_qualified=False,disks=disks)

    def admit(self,plan_hash,plans):
        proof=dict(self.check(),status='admitted',plan_hash=plan_hash,
            admitted_scope='serial guarded increments only' if self.execution_workers==1 else 'bounded parallel guarded increments only',queued_units=len(plans))
        return dict(proof,certificate_hash=contracts.digest(proof))


def write_json(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,default=str))
    temporary.replace(path)


class SynchronizedClient:
    """Shared control connection; worker factories still own separate clients."""
    def __init__(self,client):
        self.client=client;self.lock=RLock()
        self.query=schema.BatchedStorageQuery(self._query)
    def _query(self,*args,**kwargs):
        with self.lock:return self.client.query(*args,**kwargs)
    def close(self):
        with self.lock:return self.client.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--start-date',type=date.fromisoformat,required=True)
    p.add_argument('--end-date',type=date.fromisoformat,required=True)
    p.add_argument('--anchor-date',type=date.fromisoformat,required=True)
    p.add_argument('--conid-review-hash',required=True)
    p.add_argument('--budget-gib',type=int,default=512)
    p.add_argument('--endpoint',required=True)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--execution-workers',type=int,choices=range(1,9),default=1,
        help='Runtime-only override; retains frozen dataset configuration and plans')
    p.add_argument('--execution-envelope-gib',type=int,choices=range(16,129),default=128,
        help='Explicit per-worker engineering reservation; not a qualified population rate')
    a=p.parse_args(argv)
    if not a.run_dir.parent.is_dir():raise ValueError('Required runtime parent unavailable')
    if not 256<=a.budget_gib<=1024:raise ValueError('Explicit budget must be 256 to 1024 GiB')
    cfg=contracts.CampaignConfig(a.start_date,a.end_date,workers=1,query_threads=2,
        query_memory_bytes=2*GIB,global_memory_bytes=2*GIB,query_timeout_seconds=900,
        product_scope='market_bars',identity_policy='current_conid_retrospective_v1',conid_review_hash=a.conid_review_hash)
    request=dict(config_hash=cfg.content_hash,anchor_date=str(a.anchor_date),budget_gib=a.budget_gib,
        endpoint=a.endpoint,version='complete-market-incremental-queue-v1')
    if a.resume:
        meta=json.loads((a.run_dir/'queue.json').read_text())
        if meta['request']!=request:raise ValueError('Resume request differs from frozen queue')
    else:
        a.run_dir.mkdir();meta=None
    stop=Event();budget=None
    execution=dict(version='market-queue-execution-resources-v1',execution_workers=a.execution_workers,
        execution_memory_bytes=a.execution_workers*2*GIB,per_worker_envelope_bytes=a.execution_envelope_gib*GIB)
    def save(status,**values):
        write_json(a.run_dir/'status.json',dict(status=status,pid=os.getpid(),
            updated_at=datetime.now(timezone.utc).isoformat(),**values))
    save('preparing',scope='complete admitted universe',budget_gib=a.budget_gib,execution_resources=execution)
    execution_receipt=dict(execution,started_at=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
        frozen_request_hash=contracts.digest(request),resume=a.resume)
    receipt_path=a.run_dir/('execution-'+uuid4().hex+'.json');write_json(receipt_path,execution_receipt)
    load_env_files([a.env_file],verbose=False)
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    def clients(config=cfg):
        client=builder.Client(SimpleNamespace(env_file=a.env_file,max_threads=config.query_threads,
            max_memory_gb=config.query_memory_bytes/GIB,query_timeout=config.query_timeout_seconds),persistent=True)
        client.query=schema.BatchedStorageQuery(client.query)
        return client
    try:
        with closing(SynchronizedClient(clients())) as client,closing(open_workstation_keeper_session()) as keeper,acquisition.stop_marker(stop,a.run_dir/'stop.request'):
            with closing(KeeperClaim(keeper.client,GLOBAL_CLAIM)) as global_claim:
                installation.verify(client.query,'arte',require_tables=True)
                canonical=source.inventory(client.query,a.start_date,a.end_date)
                reporting=source.reporting_inventory(client.query,canonical)
                epoch=source.require_ready(client.query,canonical,reporting)
                snapshot=universe.load_for_config(client.query,a.start_date,a.end_date,a.anchor_date,cfg)
                partition=cohorts.partition_snapshot(snapshot,cfg,canonical['expected'],maximum_issuers=64,maximum_sessions=10000)
                if meta is None:
                    baseline=int(client.query("SELECT coalesce(sum(bytes_on_disk),0) n FROM system.parts WHERE database='arte'")[0]['n'])
                    meta=dict(request=request,source_epoch=epoch,partition=partition,baseline_bytes=baseline,
                        full_source_hash=snapshot['source_hash'],quarantine_counts=snapshot.get('quarantine_counts',{}),
                        eligibility_census=snapshot.get('eligibility_census',{}),
                        quarantine=snapshot.get('quarantine',()),historical_membership_certified=snapshot.get('historical_membership_certified'),
                        independent_existing_datasets_preserved=True,existing_receipts_reused=False,
                        cohorts=[dict(dataset_id=str(uuid4()),issuer_keys=group,status='queued') for group in partition['groups']])
                    write_json(a.run_dir/'queue.json',meta)
                elif (meta['source_epoch']!=epoch or meta['full_source_hash']!=snapshot['source_hash']
                        or meta['partition']['partition_hash']!=partition['partition_hash']):
                    raise ValueError('Frozen full population or canonical source changed')
                if [tuple(c['issuer_keys']) for c in meta['cohorts']]!=list(partition['groups']):
                    raise ValueError('Queue cohorts differ from complete frozen partition')
                budget=IncrementalBudget(client.query,meta['baseline_bytes'],a.budget_gib*GIB,stop,
                    execution_workers=a.execution_workers,execution_memory_bytes=execution['execution_memory_bytes'],
                    per_worker_envelope_bytes=execution['per_worker_envelope_bytes'])
                rules=client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
                total=len(meta['cohorts']);completed=sum(c['status']=='complete' for c in meta['cohorts'])
                print(f'Market queue | {total} cohorts | {len(partition["issuer_keys"])} issuers | incremental budget {a.budget_gib} GiB',flush=True)
                for index,child in enumerate(meta['cohorts']):
                    if child['status']=='complete':
                        with closing(KeeperClaim(keeper.client,child['dataset_id'])) as claim:
                            verification.verify(client.query,'arte',child['dataset_id'],claim,clients,
                                lambda:DictionaryClaim(keeper.client,'arte'))
                        continue
                    if stop.is_set():break
                    global_claim.check();budget.check()
                    config=replace(cfg,issuer_keys=tuple(child['issuer_keys']))
                    dataset=child['dataset_id']
                    with closing(KeeperClaim(keeper.client,dataset)) as claim:
                        exists=client.query('SELECT count() n FROM arte.research_dataset_v1 WHERE dataset_id=toUUID('+contracts.literal(dataset)+')')[0]['n']
                        if exists:
                            saved=campaign.load_plan(client.query,'arte',dataset)
                            if saved.config.content_hash!=config.content_hash:raise ValueError('Saved cohort configuration differs')
                        else:
                            selected=cohorts.select(snapshot,config.issuer_keys)
                            assembled=independent_market.assemble(config,selected,canonical,reporting,
                                recipe.market_recipe(config,rules)['definition'],rules,a.anchor_date,query=client.query,
                                guard=lambda:source.require_ready(client.query,canonical,reporting))
                            saved=SimpleNamespace(dataset_id=dataset,config=config,units=assembled['plans'],source_hash=assembled['source_hash'])
                        source_check=verification.source_guard(client.query,saved)
                        def guard():
                            global_claim.check();budget.check();source_check()
                        factory=lambda:clients(config)
                        runtime=runtime_plan.runtime(saved,claim,factory,guard)
                        launch=ExecutionLaunch(client.query,config,dataset,saved.units,saved.source_hash,claim,factory,guard,
                            budget.admit,lambda:DictionaryClaim(keeper.client,'arte'),runtime)
                        def progress(event):
                            save('running',cohort=index+1,cohorts=total,completed_cohorts=completed,
                                queued_cohorts=total-completed-1,active_cohorts=1,dataset_id=dataset,event=event,
                                quarantine_counts=meta['quarantine_counts'],execution_resources=execution)
                            if event['status'] in ('certified','failed','blocked'):
                                print(f'Cohort {index+1}/{total} | {event["product"]} {event["status"]} | {event["unit"]}',flush=True)
                        child['status']='active';write_json(a.run_dir/'queue.json',meta)
                        execution_receipt.update(dataset_id=dataset,plan_hash=launch.controller.plan_hash,
                            frozen_config_hash=config.content_hash,updated_at=datetime.now(timezone.utc).isoformat())
                        write_json(receipt_path,execution_receipt)
                        result=launch.run(stop=stop,progress=progress,execution_workers=a.execution_workers,
                            execution_memory_bytes=execution['execution_memory_bytes'])
                        child['status']=result['status'];child['seal']=result.get('seal')
                        child['errors']={k:str(v) for k,v in result.get('errors',{}).items()}
                        write_json(a.run_dir/'queue.json',meta)
                        if result['status']!='complete':break
                        completed+=1
                status='budget_paused' if budget.reason else 'interrupted' if stop.is_set() else 'complete' if completed==total else 'failed'
                save(status,completed_cohorts=completed,cohorts=total,queued_cohorts=total-completed,
                    budget_reason=budget.reason,combined_dataset_complete=False)
                return 0 if status=='complete' else 2
    except BaseException as error:
        if budget is not None and budget.reason:
            save('budget_paused',reason=budget.reason,combined_dataset_complete=False)
            return 2
        save('failed',error_type=type(error).__name__,reason=str(error)[:500]);raise


if __name__=='__main__':raise SystemExit(main())
