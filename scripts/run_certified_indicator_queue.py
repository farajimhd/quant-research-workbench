"""Consume the complete market queue with finite December indicator warmup.

Waits for strict canonical December certificates; never acquires source data.
Indicators and continuous state publish only through existing ClickHouse
producers. Missing prices, identities and sparse histories remain explicit.
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
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
from uuid import uuid4,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo

from scripts import build_market_day as builder
from scripts.run_certified_market_queue import IncrementalBudget,GIB,write_json
from pipelines.certified_research import contracts,source,universe,campaign,recipe,market,producer
from pipelines.certified_research import execution_calendar,indicator_state,warmup_state,adjustments,acquisition,installation,price_inputs
from pipelines.certified_research.identity import identity_key,LISTING,SECURITY
from pipelines.certified_research.publication import KeeperClaim,Publisher,Scope
from pipelines.certified_research.planning import MarketSession
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session

NY=ZoneInfo('America/New_York')
DECEMBER=source.exchange_sessions(date(2025,12,1),date(2025,12,31))
INITIAL=DECEMBER[-4:]
FRAMES=(100,1000,5000,10000,30000,60000,300000,3600000)


class Waiting(Exception):pass
class InsufficientHistory(Exception):pass


def frozen_queue(value):
    return contracts.digest(dict(request=value['request'],partition=value['partition'],
        full_source_hash=value['full_source_hash'],source_epoch=value['source_epoch'],
        cohorts=[dict(dataset_id=c['dataset_id'],issuer_keys=c['issuer_keys']) for c in value['cohorts']]))


def minimum_frames(day,missing=None):
    if day==DECEMBER[-1]:return FRAMES
    return tuple(sorted(set(missing or (3600000,))))


def choose_observations(rows,frames=FRAMES):
    """Deterministic last 50 valid observations, independently for each frame."""
    selected={frame:[] for frame in frames};seen=set()
    for row in rows:
        frame=int(row['resolution_ms']);day=date.fromisoformat(str(row['session_date']))
        key=(frame,day,int(row['bucket_index']))
        if (frame not in selected or day not in DECEMBER or key in seen
                or not 0<int(row['low_int'])<=int(row['close_int'])<=int(row['high_int'])):
            raise ValueError('Invalid or duplicate December seed observation')
        seen.add(key);selected[frame].append(row)
    selected={frame:tuple(sorted(values,key=lambda r:(str(r['session_date']),int(r['bucket_index'])))[-50:])
        for frame,values in selected.items()}
    return selected,{frame:50-len(values) for frame,values in selected.items() if len(values)<50}


def dated_mapping(snapshot):
    return tuple(dict(listing_key=identity_key(LISTING,i.listing_id),security_key=identity_key(SECURITY,i.security_id),
        ticker=i.ticker,start=str(i.valid_from.astimezone(NY).date()),end_exclusive=str(i.valid_to.astimezone(NY).date()))
        for i in snapshot['intervals'])


def identity_for_day(mappings,listing,day):
    found=[r for r in mappings if r['listing_key']==listing and date.fromisoformat(r['start'])<=day<date.fromisoformat(r['end_exclusive'])]
    if len(found)>1:raise ValueError('Competing dated December identities')
    return found[0] if found else None


def verified_predecessor(query,claim,dataset,row,rules):
    day=date.fromisoformat(row['day']);listing=int(row['listing_key'])
    technical=row['technical_receipt'];state_receipt=row['state_receipt'];bars=row['bars_receipt']
    publisher=Publisher(query,'arte',claim)
    if publisher.receipt(Scope(row['bars_dataset_id'],day,listing,'bars',bars['parent_hash']))!=bars:
        raise ValueError('Stored source bar receipt changed')
    if publisher.receipt(Scope(dataset,day,listing,'technical',technical['parent_hash']))!=technical:
        raise ValueError('Stored technical predecessor receipt changed')
    loaded=indicator_state.load(query,'arte',claim,Scope(dataset,day,listing,'technical_state',state_receipt['parent_hash']),
        row['recipe_hash'],row['predecessor_hash'],source_hash=row['source_hash'],rules_hash=contracts.digest(rules),frames=FRAMES)
    if loaded is None or loaded['receipt']!=state_receipt:raise ValueError('Stored predecessor state changed')
    expected=contracts.digest(dict(technical=technical['output_hash'],state=state_receipt['output_hash']))
    if expected!=row['output_hash']:raise ValueError('Stored combined predecessor fingerprint changed')
    return day,loaded['state'],expected


def main(argv=None):
    from pipelines.certified_research.retired_market import MESSAGE
    raise SystemExit(MESSAGE)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--bar-queue',type=Path,required=True)
    p.add_argument('--calendar',type=Path,required=True)
    p.add_argument('--calendar-hash',required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--endpoint',required=True)
    p.add_argument('--poll-seconds',type=int,default=30)
    p.add_argument('--resume',action='store_true')
    a=p.parse_args(argv)
    if not a.run_dir.parent.is_dir():raise ValueError('Required runtime parent unavailable')
    if not 5<=a.poll_seconds<=60:raise ValueError('Poll interval must be 5 to 60 seconds')
    bars=json.loads(a.bar_queue.read_text());queue_hash=frozen_queue(bars)
    calendar_bytes=a.calendar.read_bytes();calendar_file_hash=sha256(calendar_bytes).hexdigest()
    calendar=execution_calendar.restore(json.loads(calendar_bytes),expected_evidence_hash=a.calendar_hash)
    raw=bars['request']
    # The queue pins configuration by hash; the first stored child supplies its
    # full immutable dates, resources and reviewed identity assumptions.
    request=dict(version='finite-december-indicator-queue-v1',bar_queue_hash=queue_hash,
        calendar_hash=a.calendar_hash,calendar_file_hash=calendar_file_hash)
    if a.resume:
        ledger=json.loads((a.run_dir/'queue.json').read_text())
        if ledger['request']!=request:raise ValueError('Indicator resume differs from frozen inputs')
    else:
        a.run_dir.mkdir();ledger=dict(request=request,dataset_id=str(uuid4()),warmup_dataset_id=str(uuid4()),
            completed={},deferred={},waiting={},failed={},source_queue=str(a.bar_queue))
        write_json(a.run_dir/'queue.json',ledger)
    stop=Event();budget=None;last_status=None
    def save(status,**values):
        nonlocal last_status
        write_json(a.run_dir/'status.json',dict(status=status,pid=os.getpid(),
            updated_at=datetime.now(timezone.utc).isoformat(),completed=len(ledger['completed']),
            deferred=len(ledger['deferred']),waiting=len(ledger['waiting']),failed=max(len(ledger['failed']),int(status=='failed')),
            dataset_id=ledger['dataset_id'],**values))
        message=(status,values.get('reason'))
        if message!=last_status:
            print(f'Indicators | {status} | completed {len(ledger["completed"])} | deferred {len(ledger["deferred"])} | '+str(values.get('reason','')),flush=True)
            last_status=message
    save('preparing')
    load_env_files([a.env_file],verbose=False);os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    def clients():
        return builder.Client(SimpleNamespace(env_file=a.env_file,max_threads=2,max_memory_gb=2,query_timeout=900),persistent=True)
    try:
        with closing(clients()) as client,closing(open_workstation_keeper_session()) as keeper,acquisition.stop_marker(stop,a.run_dir/'stop.request'):
            global_id=str(uuid5(NAMESPACE_URL,'certified-research/arte/indicators/'+queue_hash))
            with closing(KeeperClaim(keeper.client,global_id)) as global_claim,closing(KeeperClaim(keeper.client,ledger['dataset_id'])) as claim,closing(KeeperClaim(keeper.client,ledger['warmup_dataset_id'])) as warmup_claim:
                installation.verify(client.query,'arte',require_tables=True)
                budget=IncrementalBudget(client.query,bars['baseline_bytes'],int(raw['budget_gib'])*GIB,stop)
                rules=tuple(client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id"))
                plans={};snapshot=None;mappings=None;states={};warmup_parents={};frozen_sources={};verified=set()
                def guard():
                    global_claim.check();claim.check();warmup_claim.check();budget.check()
                    if sha256(a.calendar.read_bytes()).hexdigest()!=calendar_file_hash:raise ValueError('Execution calendar file changed')
                    for canonical,reporting,epoch in frozen_sources.values():
                        if source.require_ready(client.query,canonical,reporting)!=epoch:raise ValueError('Pinned market source changed')
                def ready(days):
                    canonical=source.inventory(client.query,min(days),max(days),sessions=tuple(sorted(days)))
                    reporting=source.reporting_inventory(client.query,canonical)
                    if canonical['missing'] or canonical['incomplete'] or reporting['missing'] or reporting['mutating']:
                        raise Waiting('strict canonical/reporting source certificates unavailable: '+','.join(map(str,days)))
                    epoch=source.require_ready(client.query,canonical,reporting)
                    name=','.join(map(str,sorted(days)))
                    previous=ledger.setdefault('source_epochs',{}).get(name)
                    if previous is not None and previous!=epoch:raise ValueError('Frozen indicator source epoch changed')
                    if previous is None:
                        ledger['source_epochs'][name]=epoch;write_json(a.run_dir/'queue.json',ledger)
                    frozen_sources[tuple(sorted(days))]=(canonical,reporting,epoch)
                    return source.market_day_hashes(canonical,reporting)
                def ensure_snapshot(config):
                    nonlocal snapshot,mappings
                    if snapshot is None:
                        snapshot=universe.load_for_config(client.query,date(2025,12,1),config.end,
                            date.fromisoformat(raw['anchor_date']),config)
                        mappings=dated_mapping(snapshot)
                        if ledger.get('december_identity_hash',snapshot['source_hash'])!=snapshot['source_hash']:
                            raise ValueError('Pinned December identity source changed')
                        ledger['december_identity_hash']=snapshot['source_hash']
                        ledger['december_quarantine_counts']=snapshot.get('quarantine_counts',{})
                        write_json(a.run_dir/'queue.json',ledger)
                def warmup(listing,ticker,target_day,config):
                    ensure_snapshot(config)
                    if identity_for_day(mappings,listing,DECEMBER[-1]) is None:
                        raise InsufficientHistory('No proven December 31 identity; December minimum history unavailable')
                    parents=warmup_parents.setdefault(listing,{})
                    def observations():
                        rows=[]
                        for saved_scope,saved_receipt in parents.values():
                            if Publisher(client.query,'arte',warmup_claim).receipt(saved_scope)!=saved_receipt:raise ValueError('December seed parent changed')
                            rows.extend(client.query('SELECT session_date,resolution_ms,bucket_index,close_int,high_int,low_int FROM arte.research_bars_v1 WHERE '
                                +saved_scope.where(saved_receipt['attempt_id'])+' AND price_valid=1 AND extremes_valid=1 AND close_int>0 '
                                'ORDER BY resolution_ms,bucket_index DESC LIMIT 50 BY resolution_ms'))
                        return choose_observations(rows)
                    selected,missing=observations()
                    for day in reversed(DECEMBER):
                        if not missing:break
                        if day in parents:continue
                        identity=identity_for_day(mappings,listing,day)
                        if identity is None:
                            raise InsufficientHistory('Known listing lifecycle cannot supply 50 December bars per frame')
                        hashes=ready((day,));frames=minimum_frames(day,missing)
                        warm_cfg=replace(config,product_scope='market_bars',resolutions_ms=frames)
                        frozen=recipe.market_recipe(warm_cfg,rules)
                        spelling=source.resolve_spellings(client.query,(MarketSession(day=day,listing_key=listing,ticker=identity['ticker']),),guard=guard)['sessions'][0].source_ticker
                        unit=market.MarketUnit(ledger['warmup_dataset_id'],day,identity['ticker'],listing,str(uuid4()),spelling)
                        request_=producer.MarketRequest(unit,frozen['hash'],hashes[day],rules,{},'0'*64,'0'*64,
                            price_inputs.calendar_close(day),True,frozen['definition'])
                        units,handler=producer.market_execution(request_,clients,warmup_claim,guard,lambda:None,
                            query_memory_bytes=2*GIB)
                        aggregate=handler(next(u for u in units if u.product=='aggregate'),{},stop)
                        receipt=handler(next(u for u in units if u.product=='bars'),{'aggregate':aggregate},stop)
                        handler(next(u for u in units if u.product=='broker_100ms'),{'aggregate':aggregate},stop)
                        scope=Scope(unit.dataset_id,day,listing,'bars',request_.parent('bars'))
                        if Publisher(client.query,'arte',warmup_claim).receipt(scope)!=receipt:raise ValueError('December bar receipt changed')
                        parents[day]=(scope,receipt)
                        selected,missing=observations()
                        if not missing:break
                    if missing:raise InsufficientHistory('Insufficient December observed prices: '+str(missing))
                    result=warmup_state.calculate(client.query,selected,
                        lambda previous,day:execution_calendar.resolve(calendar,listing,previous,day,mappings),
                        dataset_id=ledger['dataset_id'],listing_key=listing,ticker=ticker,target_day=target_day)
                    return result['state'],result['certificate_hash']
                while not stop.is_set():
                    if frozen_queue(json.loads(a.bar_queue.read_text()))!=queue_hash:raise ValueError('Complete source bar queue changed')
                    try:ready(INITIAL)
                    except Waiting as error:
                        save('waiting_source',reason=str(error),required_sessions=list(map(str,INITIAL)));stop.wait(a.poll_seconds);continue
                    progressed=False;pending=False
                    for child in bars['cohorts']:
                        dataset=child['dataset_id']
                        if dataset not in plans:
                            if not client.query('SELECT count() n FROM arte.research_dataset_v1 WHERE dataset_id=toUUID('+contracts.literal(dataset)+')')[0]['n']:
                                pending=True;continue
                            saved=campaign.load_plan(client.query,'arte',dataset)
                            plans[dataset]=saved
                        saved=plans[dataset];config=saved.config
                        hashes=ready(tuple(source.exchange_sessions(config.start,config.end)))
                        frozen=recipe.market_recipe(config,rules)
                        for plan in (p for p in saved.units if p.unit.product=='market_aggregate'):
                            if stop.is_set():break
                            args=plan.arguments;listing=int(args['listing_key']);day=date.fromisoformat(args['day'])
                            key=f'{dataset}:{listing}:{day}'
                            if key in ledger['completed']:
                                if key not in verified:
                                    states[listing]=verified_predecessor(client.query,claim,ledger['dataset_id'],ledger['completed'][key],rules)
                                    verified.add(key)
                                continue
                            if str(listing) in ledger['deferred']:continue
                            locators=client.query('SELECT parent_hash FROM arte.research_unit_receipt_v1 WHERE dataset_id=toUUID('
                                +contracts.literal(dataset)+') AND session_date=toDate('+contracts.literal(day)+') AND listing_key='
                                +str(listing)+" AND product='bars'")
                            if not locators:pending=True;break
                            if len(locators)!=1:raise ValueError('Competing source bar authorities')
                            scope=Scope(dataset,day,listing,'bars',locators[0]['parent_hash'])
                            bars_receipt=Publisher(client.query,'arte',claim).receipt(scope)
                            try:
                                ensure_snapshot(config)
                                if listing not in states:
                                    previous=[r for r in ledger['completed'].values() if r['listing_key']==listing and date.fromisoformat(r['day'])<day]
                                    if previous:
                                        states[listing]=verified_predecessor(client.query,claim,ledger['dataset_id'],max(previous,key=lambda r:r['day']),rules)
                                if listing not in states:
                                    prior,prior_hash=warmup(listing,args['ticker'],day,config)
                                    adjustment_hash=prior_hash
                                else:
                                    previous,prior,prior_hash=states[listing]
                                    adjustment=execution_calendar.resolve(calendar,listing,previous,day,mappings)
                                    prior=adjustments.indicator_state(prior,adjustment,frames=FRAMES)
                                    adjustment_hash=adjustment.evidence_hash
                            except Waiting as error:
                                ledger['waiting'][str(listing)]=dict(reason=str(error),day=str(day));pending=True
                                write_json(a.run_dir/'queue.json',ledger);continue
                            except InsufficientHistory as error:
                                ledger['deferred'][str(listing)]=dict(reason=str(error),status='insufficient_history',first_target_day=str(day))
                                ledger['waiting'].pop(str(listing),None);write_json(a.run_dir/'queue.json',ledger);continue
                            guard();save('running',active=dict(listing_key=listing,day=str(day),ticker=args['ticker']))
                            unit=market.MarketUnit(ledger['dataset_id'],day,args['ticker'],listing,str(uuid4()),args.get('source_ticker'))
                            request_=producer.MarketRequest(unit,frozen['hash'],hashes[day],rules,prior,prior_hash,'0'*64,
                                price_inputs.calendar_close(day),False,frozen['definition'],adjustment_hash,dataset,scope.parent_hash)
                            units,handler=producer.market_execution(request_,clients,claim,guard,lambda:None,query_memory_bytes=2*GIB)
                            result=handler(next(u for u in units if u.product=='technical'),{'bars':bars_receipt},stop)
                            if indicator_state.authority(request_,bars_receipt,client.query,'arte',claim)!=result:
                                raise ValueError('Technical output/state receipt verification differs')
                            technical=result['technical_receipt']
                            loaded=indicator_state.load(client.query,'arte',claim,indicator_state.scope_for(request_,technical),
                                request_.dataset_hash,prior_hash,source_hash=request_.source_hash,rules_hash=contracts.digest(rules),frames=FRAMES)
                            states[listing]=(day,loaded['state'],result['output_hash'])
                            ledger['completed'][key]=dict(day=str(day),listing_key=listing,output_hash=result['output_hash'],
                                state_parent_hash=result['state_receipt']['parent_hash'],predecessor_hash=prior_hash,
                                recipe_hash=request_.dataset_hash,source_hash=request_.source_hash,
                                bars_dataset_id=dataset,bars_receipt=bars_receipt,
                                technical_receipt=technical,state_receipt=result['state_receipt'])
                            ledger['waiting'].pop(str(listing),None);write_json(a.run_dir/'queue.json',ledger);progressed=True
                            verified.add(key)
                    latest=json.loads(a.bar_queue.read_text())
                    if not pending and all(c['status']=='complete' for c in latest['cohorts']):
                        save('complete_with_deferred' if ledger['deferred'] else 'complete',combined_dataset_complete=False);return 0
                    save('waiting_bars' if not ledger['waiting'] else 'waiting_sparse_history',reason='Waiting for certified bar receipts or explicitly recorded earlier December sources')
                    stop.wait(a.poll_seconds if not progressed else 1)
                save('interrupted');return 2
    except BaseException as error:
        save('budget_paused' if budget is not None and budget.reason else 'failed',reason=str(error)[:500]);raise


if __name__=='__main__':raise SystemExit(main())
