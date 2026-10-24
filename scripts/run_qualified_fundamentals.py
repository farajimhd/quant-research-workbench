"""Receipt-preserving original SEC qualification and normalized ARTE publication.

The full retained document census is attempted. Blocked documents retain reasons
and never receive a qualified publication receipt. No provider acquisition.
"""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,time,threading,tarfile
from datetime import date,datetime,timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
from uuid import uuid5,NAMESPACE_URL
from scripts.run_fundamentals import Client,save,lock
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from pipelines.certified_research.contracts import digest,literal
from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
from pipelines.certified_research import sec_document_lineage as lineage,sec_document_payload as payload
from pipelines.certified_research import sec_share_source,sec_share_context,sec_fundamental_context as extraction,facts
from pipelines.certified_research import sec_fundamental_reconciliation as reconciliation,sec_fundamental_publication as originals
from pipelines.certified_research import sec_reported_publication as reported,sec_witness_publication as witness_publication
from pipelines.certified_research import sec_archive_witness,sec_public_archive_time,sec_receipt_recovery
from pipelines.certified_research.sec_source_guard import SourceGuard
from pipelines.market_sip.flatfiles import archive_staging
from pipelines.fundamentals import qualified,schema
from pipelines.fundamentals.eligibility import require_eligible


def _main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--population',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--endpoint',default='http://192.168.1.218:18123')
    parser.add_argument('--start',type=date.fromisoformat,default=date(2019,1,1))
    parser.add_argument('--end',type=date.fromisoformat,default=date(2026,10,2))
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--profile',action='store_true',help='Three real original documents, then exact receipt reuse; no full launch')
    parser.add_argument('--profile-count',type=int,default=3)
    parser.add_argument('--retry-manifest',type=Path,help='Sealed blocked-only selection in a separate run directory; never rewrites source receipts')
    parser.add_argument('--actual-gap-manifest',type=Path,help='At most 12 exact uncompleted retained census documents; all qualification checks still apply')
    parser.add_argument('--retained-gap-manifest',type=Path,help='At most 12 exact retained originals outside the old form census; reviewed population and all qualification checks required')
    parser.add_argument('--verified-download-time',action='store_true',help='Explicit bounded gap policy: availability begins at the verified complete public download, never the filing day')
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--upgrade-recipe',action='store_true',help='Archive the prior request and independently verify existing qualified receipts before reuse')
    parser.add_argument('--profile-result',type=Path)
    parser.add_argument('--family-verification',type=Path,help='Reviewed family verification report, including unresolved identity coverage')
    parser.add_argument('--archive-root',type=Path,default=Path('G:/market-data/sec_core/daily_archives'))
    parser.add_argument('--evidence-root',type=Path,default=Path('D:/market-data/prepared'))
    a=parser.parse_args();root=a.run_dir.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not 1<=a.workers<=8 or a.end<a.start:
        raise ValueError('Invalid bounded qualified campaign configuration')
    root.mkdir(parents=True,exist_ok=True);(root/'receipts').mkdir(exist_ok=True);(root/'blocked').mkdir(exist_ok=True)
    population=json.loads(a.population.read_text(encoding='utf-8-sig'));ciks=population['families']['sec_bulk']
    require_eligible('issuer_cik',ciks);pop_hash=digest(population)
    recipe=digest(dict(publisher=qualified.recipe_hash(),files={p.name:digest(p.read_text(encoding='utf-8')) for p in Path(lineage.__file__).parent.glob('sec_*.py')}))
    request=dict(population_hash=pop_hash,start=str(a.start),end=str(a.end),recipe=recipe,profile=a.profile,
        endpoint=a.endpoint,source='retained_original_SEC',destination='arte',numeric_mode='round-to-nearest')
    retry_documents=None;already_completed=[]
    if sum(bool(x) for x in (a.retry_manifest,a.actual_gap_manifest,a.retained_gap_manifest))>1:raise ValueError('Choose one explicit gap selection')
    if (a.actual_gap_manifest or a.retained_gap_manifest) and not a.profile:raise ValueError('Actual gaps use bounded profile and independent reuse validation')
    if a.verified_download_time:
        if not a.profile or not (a.actual_gap_manifest or a.retained_gap_manifest):raise ValueError('Download-time qualification requires explicit bounded gap targets')
        request['public_time_mode']='verified_download_time'
    selection_manifest=a.retry_manifest or a.actual_gap_manifest
    if selection_manifest:
        from pipelines.fundamentals.retry import select
        manifest=json.loads(selection_manifest.read_text())
        if root==Path(manifest['source_run_dir']).resolve():raise ValueError('Retry requires a separate run directory')
        retry_documents,already_completed=select(manifest,population_hash=pop_hash,start=a.start,end=a.end,runtime=runtime,actual_gaps=bool(a.actual_gap_manifest))
        if a.profile and not 1<=len(retry_documents)<=12:
            raise ValueError('Retry profiling requires 1..12 exact blocked targets')
        request['retry_manifest_hash']=manifest['manifest_hash']
    resource_rows={}
    if a.retained_gap_manifest:
        from pipelines.fundamentals.retry import retained_gaps
        manifest=json.loads(a.retained_gap_manifest.read_text())
        load_env_files(discover_clickhouse_env_files(),verbose=False)
        with closing(Client(a.endpoint)) as selection_client:
            retry_documents=retained_gaps(manifest,selection_client.query,population_hash=pop_hash,ciks=ciks,start=a.start,end=a.end)
        resource_rows=manifest.get('resources',{})
        request['retry_manifest_hash']=manifest['manifest_hash']
    if (root/'request.json').exists():
        old=json.loads((root/'request.json').read_text())
        if {k:old[k] for k in request}!=request:
            if not a.upgrade_recipe or any(old[k]!=request[k] for k in request if k!='recipe'):
                raise ValueError('Immutable campaign request differs; preserve existing receipts')
            save(root/('request-before-'+old['recipe']+'.json'),old)
            save(root/'request.json',dict(request,observed_at=old['observed_at'],previous_recipe=old['recipe']))
        observed=datetime.fromisoformat(old['observed_at'])
    else:
        observed=datetime.now(timezone.utc);save(root/'request.json',dict(request,observed_at=observed.isoformat()))
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    state=dict(state='planning',mode='QUALIFIED ORIGINAL SEC',unit='documents',counts=dict(published=0,unchanged=0,audited=0,failed=0),
        total=0,active={},queued=0,blocked=0,workers=a.workers,new_rows=0,reused_rows=0,samples={},remaining_by_family={},counts_include_active=True,
        qualification_status='Only verified documents are published; blocked coverage is not certified.')
    if retry_documents is not None:
        state.update(retry_targets=len(retry_documents),already_completed=len(already_completed))
        save(root/'source-completed-preserved.json',already_completed)
    mutex=threading.RLock();start=time.monotonic();stop=threading.Event()
    def status():
        with mutex:
            state.update(updated_at=datetime.now(timezone.utc).isoformat(),elapsed=time.monotonic()-start)
            if state['total']:state['remaining_by_family']={'SEC':state['queued']}
            save(root/'status.json',state)
    def pulse():
        while not stop.wait(5):status()
    def guard():
        if (root/'STOP').exists() or stop.is_set():raise InterruptedError('Qualified campaign stopped at durable boundary')
    with closing(Client(a.endpoint)) as client:
        selected_ciks=('0000320193','0000789019','0001089113') if a.profile else ciks
        ids=','.join(map(literal,selected_ciks));documents=[]
        for year in (() if retry_documents is not None else range(a.start.year,a.end.year+1)):
            guard();status()
            documents+=client.query('SELECT DISTINCT cik,accession_number accession,document_name document,source_archive_date day,document_type form,content_sha256,source_version_key '
                'FROM q_live.sec_filing_document_v3 PREWHERE cik IN ('+ids+') '
                f'AND source_archive_date BETWEEN toDate({literal(max(a.start,date(year,1,1)))}) AND toDate({literal(min(a.end,date(year,12,31)))}) '
                "AND document_type IN ('10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','40-F/A','EX-101.INS') ORDER BY day,cik,accession,document")
        # Select one original representation per filing. Preserve every already
        # completed representation across explicit recipe upgrades.
        annual_keys={(r['cik'],r['accession']) for r in documents if r['form'] in ('10-K','20-F','40-F')}
        completed={}
        for path in (root/'receipts').glob('*.json'):
            prior=json.loads(path.read_text())['document'];completed[(prior['cik'],prior['accession'])]=prior
        filings=defaultdict(list)
        for row in documents:filings[(row['cik'],row['accession'])].append(row)
        documents=[]
        for key,rows in filings.items():
            reports=[r for r in rows if r['form']!='EX-101.INS']
            instances=[r for r in rows if r['form']=='EX-101.INS']
            if not reports:continue
            if key in completed:
                if completed[key] not in rows:raise ValueError('Completed filing source disappeared from census')
                documents.append(completed[key])
            elif len(instances)==1:documents.extend(instances)
            else:documents.extend(reports)
        if retry_documents is not None:documents=retry_documents
        if a.profile and retry_documents is None:
            if a.profile_count not in (3,6,9,12):raise ValueError('Profile count must be 3, 6, 9 or 12')
            selected=[]
            for cik in ('0000320193','0000789019','0001089113'):
                matches=sorted([r for r in documents if r['cik']==cik and (r['cik'],r['accession']) in annual_keys],key=lambda r:(r['day'],r['accession'],r['document']))
                if not matches:raise ValueError('Representative profile issuer missing')
                count=a.profile_count//3
                for i in range(count):selected.append(matches[-1] if count==1 else matches[i*(len(matches)-1)//(count-1)])
            documents=selected
        census=dict(documents=documents,census_hash=digest(documents),selection_policy='one-original-instance-or-report-preserve-completed-v1')
        if (root/'census.json').exists():
            prior_census=json.loads((root/'census.json').read_text())
            if prior_census['census_hash']!=census['census_hash']:
                save(root/('census-before-'+prior_census['census_hash']+'.json'),prior_census)
        save(root/'census.json',census)
        state.update(total=len(documents),queued=len(documents),remaining_by_family={'SEC':len(documents)});status()
        if a.plan_only:return 0
        if not a.profile:
            if a.family_verification is None:raise ValueError('Full campaign requires family verification evidence')
            family=json.loads(a.family_verification.read_text())
            if family.get('verified') is not True or family.get('population_hash')!=pop_hash:
                raise ValueError('Family verification is missing or belongs to another population')
            if a.profile_result is None:raise ValueError('Full campaign requires real runnable-path profile')
            profile=json.loads(a.profile_result.read_text())
            if profile.get('recipe')!=recipe or profile.get('qualified',0)<3 or not profile.get('reuse_verified'):
                raise ValueError('Representative qualified profile is missing or incompatible')
        receipt_index=defaultdict(list)
        for file in sorted((a.evidence_root/'sec_daily_feed_archives').glob('*.jsonl')):
            if file.stat().st_size>256*1024**2:raise ValueError('Receipt ledger exceeds bound')
            for line in file.open(encoding='utf-8'):
                r=json.loads(line)
                if r.get('status')=='downloaded' and r.get('source_kind')=='daily_feed_archive':receipt_index[r['source_date']].append(r)
        repairs={}
        for file in sorted((a.evidence_root/'sec_downloaded_archive_validation').glob('*/archive_summary.jsonl')):
            if file.stat().st_size>256*1024**2:raise ValueError('Repair ledger exceeds bound')
            for line in file.open(encoding='utf-8'):
                r=json.loads(line)
                if r.get('repair_status')=='redownloaded_ok' and r.get('status')=='ok':
                    repairs[r['archive_date']]={k:r.get(k) for k in ('status','repair_status','archive_date','archive_path','archive_bytes','parse_errors','truncated_by_limit','members','original_error')}
        groups=defaultdict(list)
        for row in documents:groups[row['day']].append(row)
        state.update(archive_remaining=len(groups),archive_samples=[])
        state['state']='running';heartbeat=threading.Thread(target=pulse,daemon=True);heartbeat.start()
        try:
            # Archive/HTML parsing is batch CPU work. Use the connector's
            # supported 30s session budget; SUSPENDED/LOST still permanently
            # invalidates this claim and cannot rearm publication.
            with closing(open_workstation_keeper_session(timeout_seconds=30)) as keeper,closing(KeeperClaim(keeper.client,str(uuid5(NAMESPACE_URL,'qualified-original-fundamentals-v1')))) as claim, ThreadPoolExecutor(max_workers=1) as archive_pool:
                with closing(DictionaryClaim(keeper.client,'q_live')) as ddl_claim:
                    originals.install(client.query,'q_live',ddl_claim);reported.install(client.query,'q_live',ddl_claim);witness_publication.install(client.query,'q_live',ddl_claim)
                schema.install(client.query,'arte')
                def owned():guard();claim.check()
                def admission(size):
                    owned()
                    if size>64*1024**2:raise ValueError('Original evidence exceeds reserved 2GiB lane')
                    return dict(status='admitted',metadata_bytes=size,reserved_bytes=2*1024**3,maximum_lanes=a.workers)
                def blocked(row,error):
                    save(root/'blocked'/ (digest(row)+'.json'),dict(document=row,error=str(error)))
                    with mutex:state['blocked']+=1;state['queued']-=1;state['error']=str(error)[:300]
                    status()
                ordered=sorted(groups.items());prefetched={}
                def prefetch(day):
                    d=date.fromisoformat(day);relative=Path(str(d.year),f'QTR{(d.month-1)//3+1}',d.strftime('%Y%m%d')+'.nc.tar.gz')
                    source=a.archive_root/relative;target=root/'archive-staging'/relative
                    if not source.exists():return None
                    size=source.stat().st_size
                    if size>8*1024**3 or sec_receipt_recovery.select_complete(receipt_index[day],size) is None:return None
                    return archive_staging.copy_file(source,target,a.archive_root,root/'archive-staging',size,stop=stop,reserve_bytes=800*1024**3,reserve_fraction=.1)
                for day_index,(day,rows) in enumerate(ordered):
                    owned();pending=[];archive_started=time.monotonic()
                    future=prefetched.pop(day,None);prefetched_copy=None;prefetch_error=None
                    if future:
                        try:prefetched_copy=future.result()
                        except (ValueError,OSError) as error:prefetch_error=error
                    if day_index+1<len(ordered):
                        next_day,next_rows=ordered[day_index+1]
                        if any(not (root/'receipts'/(digest(r)+'.json')).exists() for r in next_rows):
                            prefetched[next_day]=archive_pool.submit(prefetch,next_day)
                    for row in rows:
                        receipt_path=root/'receipts'/(digest(row)+'.json')
                        if receipt_path.exists():
                            saved=json.loads(receipt_path.read_text())
                            if saved['document']!=row:raise ValueError('Completed document identity differs')
                            if saved['recipe']!=recipe:
                                if not a.upgrade_recipe:raise ValueError('Completed document recipe requires explicit audit migration')
                                headers=client.query("SELECT source_hash FROM arte.reference_source_v1 FINAL WHERE family='sec_original_qualified' "
                                    f"AND entity_id={literal(row['cik'])} AND source_id={literal(saved['publication']['source_id'])}")
                                if len(headers)!=1:raise ValueError('Prior qualified source is missing or ambiguous')
                                checked=qualified.publish(client.query,'q_live','arte',(row['cik'],row['accession'],row['document']),
                                    headers[0]['source_hash'],pop_hash,observed,claim,owned)
                                if checked!=dict(saved['publication'],state='reused'):raise ValueError('Prior qualified receipt failed migration readback')
                                save(root/'receipts'/(digest(row)+'.before-'+saved['recipe']+'.json'),saved)
                                saved.update(recipe=recipe,migration_audit=checked);save(receipt_path,saved)
                            with mutex:state['counts']['unchanged']+=1;state['queued']-=1;state['reused_rows']+=saved['publication']['rows']
                        else:pending.append(row)
                    if not pending:state['archive_remaining']-=1;continue
                    descriptors=[];admitted=[]
                    with mutex:state['active']['archive']=dict(family=day,stage='bounded source lineage preparation',started=time.monotonic()-start)
                    def prepare(row):
                        try:
                            with closing(Client(a.endpoint)) as lane:
                                before=lineage.inspect(lane.query,row['cik'],row['accession'],row['document'],claim,guard)
                            key=digest(dict(version=sec_share_source.VERSION,candidate_hash=before['candidate_hash'],parser=sec_share_context.VERSION))
                            descriptor=dict(document=dict(document_key=key,lineage=before));descriptor['prepared_hash']=digest(descriptor)
                            return row,descriptor,None
                        except ValueError as error:return row,None,error
                    with ThreadPoolExecutor(max_workers=a.workers) as preparation_pool:
                        for offset in range(0,len(pending),2*a.workers):
                            for row,descriptor,error in preparation_pool.map(prepare,pending[offset:offset+2*a.workers]):
                                if error is not None:blocked(row,error)
                                else:descriptors.append(descriptor);admitted.append(row)
                    if not admitted:
                        with mutex:state['active'].pop('archive',None);state['archive_remaining']-=1
                        continue
                    try:
                        resource_descriptors={}
                        for row in admitted:
                            for resource in resource_rows.get(digest(row),[]):
                                _,prepared,error=prepare(resource)
                                if error is not None:raise error
                                resource_descriptors[digest(resource)]=prepared
                        d=date.fromisoformat(day);relative=Path(str(d.year),f'QTR{(d.month-1)//3+1}',d.strftime('%Y%m%d')+'.nc.tar.gz')
                        archive=a.archive_root/relative;size=archive.stat().st_size
                        candidates=receipt_index[day];receipt=sec_receipt_recovery.select_complete(candidates,size)
                        if receipt is not None:pass
                        elif len(candidates)==1 and day in repairs:
                            recovery_path=root/('recovered-'+day+'.json')
                            retained=client.query('SELECT DISTINCT r.proof FROM q_live.research_sec_archive_download_receipt_v1 r '
                                'INNER JOIN q_live.research_sec_document_archive_time_v1 w ON r.receipt_hash=w.receipt_hash '
                                'WHERE r.source_date='+literal(day)+f' AND r.byte_size={size} LIMIT 3')
                            verified=[]
                            for entry in retained:
                                prior=json.loads(entry['proof'])
                                if prior.get('status')=='recovered_verified_representation':
                                    sec_receipt_recovery.validate(prior)
                                    if prior['recovery']['original_receipt']==candidates[0]:verified.append(prior)
                            if len(verified)>1:raise ValueError('Competing recovered archive receipts')
                            if verified:
                                if recovery_path.exists() and json.loads(recovery_path.read_text())!=verified[0]:
                                    save(root/('recovered-'+day+'-superseded-local.json'),json.loads(recovery_path.read_text()))
                                save(recovery_path,verified[0])
                            if recovery_path.exists():
                                receipt=json.loads(recovery_path.read_text());sec_receipt_recovery.validate(receipt)
                                if receipt['recovery']['original_receipt']!=candidates[0]:raise ValueError('Recovered source receipt changed')
                            else:
                                receipt=sec_receipt_recovery.recover(archive,candidates[0],repairs[day],guard=owned)
                                save(recovery_path,receipt)
                        else:raise ValueError('Archive download evidence is missing or ambiguous')
                        cache=root/'archive-staging';target=cache/relative
                        with mutex:state['active']['archive']=dict(family=day,stage='copy and archive byte verification',started=time.monotonic()-start)
                        if prefetch_error is not None:raise prefetch_error
                        copied=prefetched_copy
                        if copied is None:copied=archive_staging.copy_file(archive,target,a.archive_root,cache,size,stop=stop,reserve_bytes=800*1024**3,reserve_fraction=.1)
                        # Every selected member is checked in one archive pass.
                        if copied.get('status')=='cached':
                            # The independent witness reader hashes the entire
                            # cached container while reading selected members.
                            copied=dict(status='staged',bytes=size,sha256=receipt['sha256'])
                        if copied.get('sha256')!=receipt['sha256']:raise ValueError('Staged archive differs from qualified receipt')
                        original_cache=root/'original-documents';original_cache.mkdir(exist_ok=True)
                        def original_sink(witness,text):
                            from hashlib import sha256
                            raw=text.encode('utf-8')
                            if len(raw)>64*1024**2 or sha256(raw).hexdigest()!=witness['document_hash']:
                                raise ValueError('Original document exceeds bound or differs')
                            (original_cache/(witness['document_hash']+'.html')).write_bytes(raw)
                        all_descriptors=descriptors+list(resource_descriptors.values())
                        all_witnesses=sec_archive_witness.inspect_archives(target,all_descriptors,stop=stop,staging_receipt=copied,
                            maximum_member_bytes=4*1024**3,maximum_archive_bytes=8*1024**3,
                            maximum_scan_bytes=64*1024**3,original_document_sink=original_sink)
                        witnesses=all_witnesses[:len(descriptors)]
                        resource_witnesses=dict(zip(resource_descriptors,all_witnesses[len(descriptors):]))
                    except (ValueError,OSError,EOFError,tarfile.TarError) as error:
                        for row in admitted:blocked(row,error)
                        with mutex:state['active'].pop('archive',None)
                        state['archive_remaining']-=1
                        continue
                    with mutex:
                        state['active'].pop('archive',None)
                        state['archive_samples'].append(time.monotonic()-archive_started)
                        state['archive_remaining']-=1
                    witness_lock=threading.Lock()
                    def unit(row,descriptor,witness):
                        own=Client(a.endpoint);began=time.monotonic();request_tuple=(row['cik'],row['accession'],row['document']);token=digest(row)
                        stages={};last_stage=[None,began]
                        def update(stage):
                            now=time.monotonic()
                            if last_stage[0] is not None:stages[last_stage[0]]=now-last_stage[1]
                            last_stage[:]=[stage,now]
                            with mutex:state['active'][token]=dict(family=row['cik'],stage=stage,started=began-start)
                        bulk=None
                        def source_audit():
                            owned()
                            # Exact current lineage remains a publication guard.
                            if lineage.inspect(own.query,*request_tuple,claim,guard)!=descriptor['document']['lineage']:
                                raise ValueError('Original source lineage changed')
                            for resource in resource_rows.get(token,[]):
                                if lineage.inspect(own.query,resource['cik'],resource['accession'],resource['document'],claim,guard)!=resource_descriptors[digest(resource)]['document']['lineage']:
                                    raise ValueError('Supplemental source lineage changed')
                            if bulk is not None and digest(sorted(reconciliation.load_bulk(own.query,*request_tuple[:2],tags),key=lambda r:r['source_record']))!=digest(sorted(bulk,key=lambda r:r['source_record'])):
                                raise ValueError('Retained bulk reconciliation evidence changed')
                        stable=SourceGuard(own.query,source_audit,owned)
                        try:
                            update('public time and original extraction')
                            mode=('official_feed_representation_day' if isinstance(witness.get('filing_date_change'),str)
                                  and witness['filing_date_change']<witness['source_archive_date'] else 'official_filing_day')
                            if a.verified_download_time:mode='verified_download_time'
                            qualified_time=sec_public_archive_time.qualify(witness,receipt,mode=mode)
                            # All documents of a day share one immutable receipt.
                            # Serialize its check/insert/readback under the campaign claim.
                            with witness_lock:
                                witness_publication.Publisher(own.query,'q_live',claim,stable,admission).publish((witness,),(qualified_time,),receipt)
                            if (original_cache/(witness['document_hash']+'.html')).exists():
                                from hashlib import sha256
                                raw=(original_cache/(witness['document_hash']+'.html')).read_bytes()
                                if len(raw)>64*1024**2 or sha256(raw).hexdigest()!=witness['document_hash']:
                                    raise ValueError('Captured original document changed')
                                text=raw.decode('utf-8');del raw
                            else:
                                text=payload.load(own.query,*request_tuple,witness['document_hash'],maximum_bytes=64*1024**2,guard=owned)
                            resources=[];resource_proofs=[]
                            for resource in resource_rows.get(token,[]):
                                rw=resource_witnesses[digest(resource)]
                                rq=sec_public_archive_time.qualify(rw,receipt,mode=mode)
                                resources.append(dict(payload=(original_cache/(rw['document_hash']+'.html')).read_bytes().decode('utf-8'),source_text_hash=rw['document_hash']))
                                resource_proofs.append(dict(witness=rw,qualification=rq,receipt=receipt,
                                    identity={k:resource[k] for k in ('cik','accession','document')}))
                            extracted=extraction.extract(text,witness['document_hash'],row['cik'],maximum_bytes=64*1024**2,resource_documents=resources);del text,resources
                            if resource_proofs:
                                from pipelines.certified_research import sec_document_set
                                extracted['resource_document_proofs']=resource_proofs
                                sec_document_set.validate(extracted,witness,qualified_time['available_at'])
                                extracted['extraction_hash']=digest({k:v for k,v in extracted.items() if k!='extraction_hash'})
                                with witness_lock:
                                    witness_publication.Publisher(own.query,'q_live',claim,stable,admission).publish(
                                        tuple(p['witness'] for p in resource_proofs),tuple(p['qualification'] for p in resource_proofs),receipt)
                            tags=tuple(sorted({t for m in facts.catalog() for t in m.tags}))
                            bulk=reconciliation.load_bulk(own.query,*request_tuple[:2],tags)
                            stable.signature=None
                            update('normalized original evidence and precision')
                            original=originals.capture(own.query,'q_live',extracted,bulk,witness,qualified_time,receipt,claim,stable,admission)
                            projection=reported.capture(own.query,'q_live',extracted,witness,qualified_time,receipt,claim,stable,admission,
                                numeric_mode='round-to-nearest',source_reconciliation_hash=original['reconciliation_hash'])
                            update('qualified ARTE publication and readback')
                            result=qualified.publish(own.query,'q_live','arte',request_tuple,projection['binding_hash'],pop_hash,observed,claim,stable)
                            reuse=True
                            if a.profile:
                                again=qualified.publish(own.query,'q_live','arte',request_tuple,projection['binding_hash'],pop_hash,observed,claim,stable)
                                reuse=again['state']=='reused'
                                if not reuse:raise ValueError('Qualified profile failed receipt reuse')
                            update('complete')
                            record=dict(document=row,recipe=recipe,publication=result,seconds=time.monotonic()-began,reuse_verified=reuse,stage_seconds=stages,
                                source_guard=dict(checks=stable.checks,cache_hits=stable.cache_hits,full_audits=stable.full_audits))
                            if extracted.get('version')=='sec-reviewed-html-context-v1':
                                record.update(qualification_scope='reviewed_html_observations',whole_document_coverage=False,
                                    html_plan_hash=extracted['html_plan']['plan_hash'])
                            save(root/'receipts'/(token+'.json'),record)
                            with mutex:
                                reused=result['state']=='reused'
                                state['counts']['unchanged' if reused else 'published']+=1;state['queued']-=1
                                state['reused_rows' if reused else 'new_rows']+=result['rows']
                                if not reused:state['samples'].setdefault('SEC',{'published':[]})['published'].append(record['seconds'])
                        except ValueError as error:blocked(row,error)
                        finally:
                            own.close()
                            with mutex:state['active'].pop(token,None)
                            status()
                    with ThreadPoolExecutor(max_workers=a.workers) as pool:
                        values=iter(zip(admitted,descriptors,witnesses));futures=set();exhausted=False
                        while futures or not exhausted:
                            while not exhausted and len(futures)<2*a.workers:
                                item=next(values,None)
                                if item is None:exhausted=True
                                else:futures.add(pool.submit(unit,*item))
                            if futures:
                                done,futures=wait(futures,return_when=FIRST_COMPLETED)
                                for future in done:future.result();owned()
                    if target.resolve().is_relative_to((root/'archive-staging').resolve()):target.unlink()
                    state['remaining_by_family']={'SEC':state['queued']};status()
            state['state']='blocked' if state['blocked'] else 'complete'
            save(root/'completion.json',dict(recipe=recipe,qualified=state['counts']['published']+state['counts']['unchanged'],
                blocked=state['blocked'],reuse_verified=a.profile and state['blocked']==0,seconds=time.monotonic()-start,
                full_scope=not a.profile and not a.retry_manifest,already_completed=len(already_completed)))
            status();return 1 if state['blocked'] else 0
        except BaseException as error:
            state.update(state='stopped' if isinstance(error,InterruptedError) else 'failed',error=str(error)[:500]);status();raise
        finally:stop.set();heartbeat.join(timeout=6)


def main():
    runtime=Path('D:/TradingML/runtimes')
    if not runtime.is_dir():raise ValueError('Required runtime root is unavailable')
    with lock(runtime/'qualified-fundamentals.lock'):
        try:return _main()
        except Exception as error:
            if '--run-dir' in sys.argv:
                root=Path(sys.argv[sys.argv.index('--run-dir')+1]).resolve()
                if root.is_relative_to(runtime.resolve()) and (root/'status.json').exists():
                    state=json.loads((root/'status.json').read_text())
                    state.update(state='failed',error=str(error)[:500],updated_at=datetime.now(timezone.utc).isoformat())
                    state['counts']['failed']=max(1,state['counts'].get('failed',0));save(root/'status.json',state)
            raise


if __name__=='__main__':raise SystemExit(main())
