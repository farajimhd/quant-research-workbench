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
from concurrent.futures import ThreadPoolExecutor,as_completed
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
from pipelines.market_sip.flatfiles import archive_staging
from pipelines.fundamentals import qualified,schema
from pipelines.fundamentals.eligibility import require_eligible


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--population',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--endpoint',default='http://192.168.1.218:18123')
    parser.add_argument('--start',type=date.fromisoformat,default=date(2019,1,1))
    parser.add_argument('--end',type=date.fromisoformat,default=date(2026,10,2))
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--profile',action='store_true',help='Three real original documents, then exact receipt reuse; no full launch')
    parser.add_argument('--plan-only',action='store_true')
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
    if (root/'request.json').exists():
        old=json.loads((root/'request.json').read_text())
        if {k:old[k] for k in request}!=request:raise ValueError('Immutable campaign request differs; preserve existing receipts')
        observed=datetime.fromisoformat(old['observed_at'])
    else:
        observed=datetime.now(timezone.utc);save(root/'request.json',dict(request,observed_at=observed.isoformat()))
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    state=dict(state='planning',mode='QUALIFIED ORIGINAL SEC',unit='documents',counts=dict(published=0,unchanged=0,audited=0,failed=0),
        total=0,active={},queued=0,blocked=0,workers=a.workers,new_rows=0,reused_rows=0,samples={},remaining_by_family={},
        qualification_status='Only verified documents are published; blocked coverage is not certified.')
    mutex=threading.RLock();start=time.monotonic();stop=threading.Event()
    def status():
        with mutex:
            state.update(updated_at=datetime.now(timezone.utc).isoformat(),elapsed=time.monotonic()-start)
            save(root/'status.json',state)
    def pulse():
        while not stop.wait(5):status()
    def guard():
        if (root/'STOP').exists() or stop.is_set():raise InterruptedError('Qualified campaign stopped at durable boundary')
    with lock(runtime/'qualified-fundamentals.lock'),closing(Client(a.endpoint)) as client:
        ids=','.join(map(literal,ciks));documents=[]
        for year in range(a.start.year,a.end.year+1):
            guard();status()
            documents+=client.query('SELECT DISTINCT cik,accession_number accession,document_name document,source_archive_date day,document_type form,content_sha256,source_version_key '
                'FROM q_live.sec_filing_document_v3 FINAL WHERE cik IN ('+ids+') '
                f'AND source_archive_date BETWEEN toDate({literal(max(a.start,date(year,1,1)))}) AND toDate({literal(min(a.end,date(year,12,31)))}) '
                "AND document_type IN ('10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','40-F/A') ORDER BY day,cik,accession,document")
        if a.profile:
            selected=[]
            for cik in ('0000320193','0000789019','0001089113'):
                matches=[r for r in documents if r['cik']==cik and r['form'] in ('10-K','20-F','40-F')]
                if not matches:raise ValueError('Representative profile issuer missing')
                selected.append(max(matches,key=lambda r:(r['day'],r['accession'],r['document'])))
            documents=selected
        save(root/'census.json',dict(documents=documents,census_hash=digest(documents)))
        state.update(total=len(documents),queued=len(documents),remaining_by_family={'SEC':len(documents)});status()
        if a.plan_only:return 0
        if not a.profile:
            if a.family_verification is None:raise ValueError('Full campaign requires family verification evidence')
            family=json.loads(a.family_verification.read_text())
            if family.get('verified') is not True or family.get('population_hash')!=pop_hash:
                raise ValueError('Family verification is missing or belongs to another population')
            if a.profile_result is None:raise ValueError('Full campaign requires real runnable-path profile')
            profile=json.loads(a.profile_result.read_text())
            if profile.get('recipe')!=recipe or profile.get('qualified')!=3 or not profile.get('reuse_verified'):
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
        state['state']='running';heartbeat=threading.Thread(target=pulse,daemon=True);heartbeat.start()
        try:
            with closing(open_workstation_keeper_session()) as keeper,closing(KeeperClaim(keeper.client,str(uuid5(NAMESPACE_URL,'qualified-original-fundamentals-v1')))) as claim:
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
                for day,rows in sorted(groups.items()):
                    owned();pending=[]
                    for row in rows:
                        receipt_path=root/'receipts'/(digest(row)+'.json')
                        if receipt_path.exists():
                            saved=json.loads(receipt_path.read_text())
                            if saved['document']!=row or saved['recipe']!=recipe:raise ValueError('Completed document receipt differs')
                            with mutex:state['counts']['unchanged']+=1;state['queued']-=1;state['reused_rows']+=saved['publication']['rows']
                        else:pending.append(row)
                    if not pending:continue
                    descriptors=[];admitted=[]
                    for row in pending:
                        try:
                            before=lineage.inspect(client.query,row['cik'],row['accession'],row['document'],claim,guard)
                            key=digest(dict(version=sec_share_source.VERSION,candidate_hash=before['candidate_hash'],parser=sec_share_context.VERSION))
                            descriptor=dict(document=dict(document_key=key,lineage=before));descriptor['prepared_hash']=digest(descriptor)
                            descriptors.append(descriptor);admitted.append(row)
                        except ValueError as error:blocked(row,error)
                    if not admitted:continue
                    try:
                        d=date.fromisoformat(day);relative=Path(str(d.year),f'QTR{(d.month-1)//3+1}',d.strftime('%Y%m%d')+'.nc.tar.gz')
                        archive=a.archive_root/relative;size=archive.stat().st_size
                        candidates=receipt_index[day];matching={digest(sec_receipt_recovery.canonical(r)):r for r in candidates if r['byte_size']==size}
                        if len(matching)==1:receipt=sec_receipt_recovery.canonical(next(iter(matching.values())))
                        elif not matching and len(candidates)==1 and day in repairs:
                            recovery_path=root/('recovered-'+day+'.json')
                            if recovery_path.exists():
                                receipt=json.loads(recovery_path.read_text());sec_receipt_recovery.validate(receipt)
                                if receipt['recovery']['original_receipt']!=candidates[0]:raise ValueError('Recovered source receipt changed')
                            else:
                                receipt=sec_receipt_recovery.recover(archive,candidates[0],repairs[day],guard=owned)
                                save(recovery_path,receipt)
                        else:raise ValueError('Archive download evidence is missing or ambiguous')
                        cache=root/'archive-staging';target=cache/relative
                        with mutex:state['active']['archive']=dict(family=day,stage='copy and archive byte verification',started=time.monotonic()-start)
                        copied=archive_staging.copy_file(archive,target,a.archive_root,cache,size,stop=stop,reserve_bytes=800*1024**3,reserve_fraction=.1)
                        # Every selected member is checked in one archive pass.
                        if copied.get('status')=='cached':
                            from hashlib import file_digest
                            with target.open('rb') as stream:checksum=file_digest(stream,'sha256').hexdigest()
                            copied=dict(status='staged',bytes=size,sha256=checksum)
                        if copied.get('sha256')!=receipt['sha256']:raise ValueError('Staged archive differs from qualified receipt')
                        witnesses=sec_archive_witness.inspect_archives(target,descriptors,stop=stop,staging_receipt=copied,
                            maximum_member_bytes=256*1024**2)
                    except (ValueError,OSError,EOFError,tarfile.TarError) as error:
                        for row in admitted:blocked(row,error)
                        with mutex:state['active'].pop('archive',None)
                        continue
                    with mutex:state['active'].pop('archive',None)
                    def unit(row,descriptor,witness):
                        own=Client(a.endpoint);began=time.monotonic();request_tuple=(row['cik'],row['accession'],row['document']);token=digest(row)
                        stages={};last_stage=[None,began]
                        def update(stage):
                            now=time.monotonic()
                            if last_stage[0] is not None:stages[last_stage[0]]=now-last_stage[1]
                            last_stage[:]=[stage,now]
                            with mutex:state['active'][token]=dict(family=row['cik'],stage=stage,started=began-start)
                        bulk=None
                        def stable():
                            owned()
                            # Exact current lineage remains a publication guard.
                            if lineage.inspect(own.query,*request_tuple,claim,guard)!=descriptor['document']['lineage']:
                                raise ValueError('Original source lineage changed')
                            if bulk is not None and reconciliation.load_bulk(own.query,*request_tuple[:2],tags)!=bulk:
                                raise ValueError('Retained bulk reconciliation evidence changed')
                        try:
                            update('public time and original extraction')
                            qualified_time=sec_public_archive_time.qualify(witness,receipt,mode='official_filing_day')
                            witness_publication.Publisher(own.query,'q_live',claim,stable,admission).publish((witness,),(qualified_time,),receipt)
                            text=payload.load(own.query,*request_tuple,witness['document_hash'],maximum_bytes=64*1024**2,guard=owned)
                            extracted=extraction.extract(text,witness['document_hash'],row['cik'],maximum_bytes=64*1024**2);del text
                            tags=tuple(sorted({t for m in facts.catalog() for t in m.tags}))
                            bulk=reconciliation.load_bulk(own.query,*request_tuple[:2],tags)
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
                            record=dict(document=row,recipe=recipe,publication=result,seconds=time.monotonic()-began,reuse_verified=reuse,stage_seconds=stages)
                            save(root/'receipts'/(token+'.json'),record)
                            with mutex:
                                state['counts']['published']+=1;state['queued']-=1;state['new_rows']+=result['rows']
                                state['samples'].setdefault('SEC',{'published':[]})['published'].append(record['seconds'])
                        except ValueError as error:blocked(row,error)
                        finally:
                            own.close()
                            with mutex:state['active'].pop(token,None)
                            status()
                    with ThreadPoolExecutor(max_workers=a.workers) as pool:
                        futures=[pool.submit(unit,*values) for values in zip(admitted,descriptors,witnesses)]
                        for future in as_completed(futures):future.result();owned()
                    if target.resolve().is_relative_to((root/'archive-staging').resolve()):target.unlink()
                    state['remaining_by_family']={'SEC':state['queued']};status()
            state['state']='blocked' if state['blocked'] else 'complete'
            save(root/'completion.json',dict(recipe=recipe,qualified=state['counts']['published']+state['counts']['unchanged'],
                blocked=state['blocked'],reuse_verified=a.profile and state['blocked']==0,seconds=time.monotonic()-start,full_scope=not a.profile))
            status();return 1 if state['blocked'] else 0
        except BaseException as error:
            state.update(state='stopped' if isinstance(error,InterruptedError) else 'failed',error=str(error)[:500]);status();raise
        finally:stop.set();heartbeat.join(timeout=6)


if __name__=='__main__':raise SystemExit(main())
