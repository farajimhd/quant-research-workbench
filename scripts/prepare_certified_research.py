#!/usr/bin/env python3
"""Inspect research contracts or prepare canonical source prerequisites.

This entry point is under implementation. Inspection and saved verification
do not start a campaign. Source preparation owns bounded acquisition/reporting
repair. Launch discovers/prepares a new graph; run/resume execute an installed
immutable plan. All routes require qualified capacity and complete authority.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
from uuid import UUID

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from pipelines.certified_research import contracts,schema,source,short_sql,campaign,acquisition,capacity,universe,installation


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('schema','install-schema','preflight','plan','plan-cohorts','status','stored-plan','prepare-sources','reconcile-conids','review-conid-issues','audit-sec-submissions','qualify-sec-lineage','verify','measure-storage','measure-sec-extraction','estimate-storage','launch','launch-collection','backfill-v7','run','resume','resume-collection','verify-collection'))
    p.add_argument('--publish-conid-mappings',action='store_true',help='reconcile-conids/review-conid-issues: publish an exact retained identity snapshot to q_live; does not alter live admission')
    p.add_argument('--case-details',action='store_true',help='review-conid-issues: show every per-entity decision in text output')
    p.add_argument('--case-ticker',action='append',default=[],help='review-conid-issues: filter displayed decisions by exact wire ticker; does not narrow review or publication')
    p.add_argument('--conid-review-hash',help='Explicit completed conid case review used by batch universe preparation; never promotes unresolved dated membership')
    p.add_argument('--collection',action='store_true',help='estimate-storage only: inspect a stored collection instead of a single dataset')
    p.add_argument('--dataset-id',type=UUID)
    p.add_argument('--source-dataset-id',type=UUID,help='Qualified source plan/bar dataset for backfill-v7; never modified')
    p.add_argument('--backfill-start-date',type=date.fromisoformat,help='Optional narrower target range inside the source plan')
    p.add_argument('--backfill-end-date',type=date.fromisoformat,help='Optional narrower target range inside the source plan')
    p.add_argument('--database',default='arte')
    p.add_argument('--start-date',type=date.fromisoformat,default=date(2026,1,1))
    p.add_argument('--end-date',type=date.fromisoformat)
    p.add_argument('--context-start-date',type=date.fromisoformat,help='Explicit earlier source prerequisite bound; target start is unchanged')
    p.add_argument('--rebuild-origin-date',type=date.fromisoformat,help='Explicit versioned empty-state origin if compatible causal seeds are absent; every subsequent session is replayed')
    p.add_argument('--fundamental-start-date',type=date.fromisoformat,help='Fresh-launch SEC context coverage bound')
    p.add_argument('--universe-anchor-date',type=date.fromisoformat,default=date.today(),help='Last allowed 2026-onward conid observation date')
    p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    p.add_argument('--capacity-profile',type=Path,help='Bounded metadata configuration containing externally qualified pilot rates; required for run/resume')
    p.add_argument('--measurement-tables',type=lambda value:tuple(value.split(',')),help='measure-storage only: isolated normalized output tables; default common output set')
    p.add_argument('--observe-storage',action='store_true',help='run/resume only: bounded isolated pilot market phase capture; does not qualify peak bounds')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--query-threads',type=int,default=2)
    p.add_argument('--query-memory-gib',type=int,default=2)
    p.add_argument('--query-timeout-seconds',type=int,default=900,help='Bounded data-query deadline, 1 to 3600 seconds; frozen for resumed campaigns')
    p.add_argument('--global-memory-gib',type=int,default=16)
    def integer_list(value):
        try:values=tuple(int(v) for v in value.split(','))
        except ValueError:raise argparse.ArgumentTypeError('Use comma-separated positive integers')
        if not values or tuple(sorted(set(values)))!=values or any(v<=0 for v in values):
            raise argparse.ArgumentTypeError('Values must be sorted, unique and positive')
        return values
    p.add_argument('--volume-windows-seconds',type=integer_list,default=(10,60),help='Rolling market feature requirements for new planning')
    p.add_argument('--short-windows-sessions',type=integer_list,default=(20,),help='Short-pressure session lookbacks for new planning')
    p.add_argument('--resolutions-ms',type=integer_list,default=(100,1000,5000,10000,30000,60000,300000,3600000),help='New-plan bar resolutions; 1000 is required by causal V7')
    p.add_argument('--issuer-keys',type=integer_list,default=(),help='Explicit bounded issuer cohort; preserves all its securities/history after full-universe admission')
    p.add_argument('--cohort-max-issuers',type=int,default=128,help='plan-cohorts issuer bound, maximum 256')
    p.add_argument('--cohort-max-sessions',type=int,default=200000,help='plan-cohorts context/listing-day budget, maximum 250000')
    p.add_argument('--format',choices=('text','json','sql'),default='text')
    p.add_argument('--acquire-missing',action='store_true',help='Opt in to the existing flatfile downloader after Massive resubscription; acquire missing appendable sources before plan freeze')
    p.add_argument('--acquire-sec-missing',action='store_true',help='Explicit opt-in to bounded SEC catch-up before graph freezing; independent of Massive acquisition')
    p.add_argument('--repair-retained-symbols',action='store_true',help='Restore missing intervals from complete retained Gateway ticker history; no provider requests')
    p.add_argument('--retained-figi',action='append',default=[],help='Explicit stable FIGI for retained symbol repair; repeat at most 1000 times')
    p.add_argument('--sec-runtime-root-ch',help='CH-readable alias for the SEC runtime folder; verified before acquisition')
    def sec_share_document(value):
        from pipelines.certified_research.sec_share_preparation import request
        try:return request(value)
        except ValueError as error:raise argparse.ArgumentTypeError(str(error)) from error
    p.add_argument('--sec-share-document',type=sec_share_document,action='append',default=[],
        help='Retained SEC source candidate: CIK,ACCESSION,DOCUMENT_NAME; requires qualified sec_share_source capacity rate, never certifies allocation')
    def census_cik(value):
        import re
        if not re.fullmatch('0*[1-9][0-9]{0,9}',value):raise argparse.ArgumentTypeError('Use a positive SEC CIK')
        return str(int(value))
    p.add_argument('--sec-census-cik',type=census_cik,action='append',default=[],help='Discover retained primary documents for explicit issuer scope; repeat up to 256 times')
    p.add_argument('--sec-census-forms',type=lambda value:tuple(value.split(',')),help='Explicit comma-separated filing forms for retained document discovery')
    p.add_argument('--reconcile-sec-submissions',action='store_true',help='Require retained filings to match independent retained bulk submissions; snapshot completeness remains unproven')
    p.add_argument('--measurement-sec-document',type=sec_share_document,action='append',default=[],help='measure-sec-extraction only: CIK,ACCESSION,DOCUMENT_NAME; repeat up to eight times')
    p.add_argument('--compare-sec-bulk',action='store_true',help='measure-sec-extraction only: bounded read-only original/bulk value comparison, without public-time qualification')
    p.add_argument('--verify-sec-archives',action='store_true',help='Verify selected SEC documents against retained G archives and publish public-time evidence; no downloads')
    p.add_argument('--prepare-sec-fundamental-evidence',action='store_true',help='Capture original fundamental contexts and bulk comparisons after archive verification; requires measured sec_fundamental_source capacity')
    p.add_argument('--sec-numeric-mode',choices=('round-to-nearest','truncation'),help='Select reported projection publication or an existing mode for automatic fresh-launch SEC scope binding')
    p.add_argument('--sec-archive-canonical-root',type=Path,default=Path('D:/market-data/sec_core/daily_archives'))
    p.add_argument('--sec-archive-backup-root',type=Path,help='Existing G backup root containing YEAR/QTRn SEC archives')
    p.add_argument('--sec-download-receipts',type=Path,help='Original downloader JSONL ledger with successful hashes and HTTP Last-Modified')
    p.add_argument('--sec-public-time-mode',choices=('acceptance_checked_day','official_filing_day'),default='acceptance_checked_day')
    def identity_point(value):
        from pipelines.certified_research.historical_identity import AcquisitionHandlers
        from types import SimpleNamespace
        fields=value.split(',')
        if len(fields) not in (3,4):raise argparse.ArgumentTypeError('Use DATE,TICKER,COMPOSITE_FIGI[,CIK]')
        request=dict(zip(('as_of','ticker','composite_figi','cik'),fields))
        try:AcquisitionHandlers.inputs(SimpleNamespace(unit=SimpleNamespace(product='identity_observation'),arguments=request))
        except (ValueError,TypeError) as error:raise argparse.ArgumentTypeError(str(error))
        return request
    p.add_argument('--identity-point',type=identity_point,action='append',default=[],help='Bounded dated identity prerequisite: DATE,TICKER,COMPOSITE_FIGI[,CIK]; repeat at most 1000 times')
    p.add_argument('--acquire-identity-missing',action='store_true',help='Explicit future opt-in to Reference Gateway dated overview acquisition; disabled by default, never SIP REST recovery')
    p.add_argument('--stage-archive',action='store_true',help='Stage new-source G files on D inside the canonical updater')
    p.add_argument('--recover-retained-reporting',action='store_true',help='Authorized ingestion-only original-clock recovery from retained G/D trades; no REST')
    p.add_argument('--staging-reserve-gib',type=int,default=256,help='Minimum D free-space reserve')
    p.add_argument('--staging-reserve-fraction',type=float,default=.10,help='D capacity fraction reserved; larger of both reserve bounds applies')
    p.add_argument('--staging-chunk-gib',type=int,default=256,help='Maximum retained-file chunk; 0 uses all available headroom')
    p.add_argument('--runtime-root',type=Path,default=acquisition.RUNTIME/'certified-research')
    p.add_argument('--cache-root',type=Path,default=acquisition.CACHE)
    p.add_argument('--archive-root',type=Path,default=acquisition.ARCHIVE)
    p.add_argument('--clickhouse-cache-root',default='/mnt/d/market-data/flatfiles/us_stocks_sip')
    p.add_argument('--hydrate-missing-trades',action='store_true',help='Deprecated: imported-day repair requires canonical sidecars')
    args=p.parse_args(argv)
    if args.reconcile_sec_submissions and not args.sec_census_cik:
        p.error('--reconcile-sec-submissions requires --sec-census-cik and --sec-census-forms')
    if args.sec_census_cik or args.sec_census_forms is not None:
        import re
        if (args.command not in ('prepare-sources','launch','launch-collection','audit-sec-submissions') or args.sec_share_document
                or not 1<=len(args.sec_census_cik)<=256 or len(set(args.sec_census_cik))!=len(args.sec_census_cik)
                or not args.sec_census_forms or len(args.sec_census_forms)>32 or len(set(args.sec_census_forms))!=len(args.sec_census_forms)
                or any(not re.fullmatch('[A-Z0-9 -]{1,24}(?:/A)?',form) for form in args.sec_census_forms)
                or args.fundamental_start_date is None or (args.command!='audit-sec-submissions' and args.capacity_profile is None)):
            p.error('SEC census selection requires fresh source preparation, unique issuers, explicit forms/context start and capacity profile; cannot mix manual documents')
    if args.command=='audit-sec-submissions' and (not args.sec_census_cik or args.end_date is None):
        p.error('audit-sec-submissions requires explicit end date, SEC census issuers, forms and fundamental context start')
    if args.command=='audit-sec-submissions' and (args.acquire_missing or args.stage_archive or args.recover_retained_reporting
            or args.verify_sec_archives or args.prepare_sec_fundamental_evidence):
        p.error('audit-sec-submissions is read-only; acquisition and preparation flags belong to prepare-sources')
    if args.sec_numeric_mode is not None and not args.prepare_sec_fundamental_evidence and args.command not in ('launch','launch-collection'):
        p.error('--sec-numeric-mode requires fundamental evidence preparation or a fresh launch; frozen resume uses stored bindings')
    if args.compare_sec_bulk and args.command!='measure-sec-extraction':p.error('--compare-sec-bulk belongs to measure-sec-extraction')
    if args.command=='measure-sec-extraction' or args.measurement_sec_document:
        if (args.command!='measure-sec-extraction' or not 1<=len(args.measurement_sec_document)<=8
                or len(set(args.measurement_sec_document))!=len(args.measurement_sec_document)
                or args.acquire_missing or args.acquire_sec_missing or args.stage_archive):
            p.error('SEC extraction measurement requires 1 to 8 unique measurement documents and cannot acquire sources')
    if args.prepare_sec_fundamental_evidence and not args.verify_sec_archives:
        p.error('Original fundamental evidence requires --verify-sec-archives and selected documents')
    if args.verify_sec_archives or args.sec_archive_backup_root is not None or args.sec_download_receipts is not None:
        if (not args.verify_sec_archives or not (args.sec_share_document or args.sec_census_cik) or args.sec_archive_backup_root is None
                or args.sec_download_receipts is None):
            p.error('SEC archive verification requires selected SEC documents, backup root and original receipt ledger')
    if args.sec_share_document:
        if (args.command not in ('prepare-sources','launch','launch-collection') or args.capacity_profile is None
                or len(args.sec_share_document)>1000 or len(set(args.sec_share_document))!=len(args.sec_share_document)):
            p.error('SEC share candidates require source preparation/fresh launch, a capacity profile and up to 1000 unique documents')
    if args.repair_retained_symbols or args.retained_figi:
        import re
        if (args.command not in ('prepare-sources','launch','launch-collection') or not args.repair_retained_symbols
                or not 1<=len(args.retained_figi)<=1000 or len(set(args.retained_figi))!=len(args.retained_figi)
                or any(not re.fullmatch('[A-Z0-9]{12}',value) for value in args.retained_figi)):
            p.error('Retained symbol repair requires source preparation/fresh launch and 1 to 1000 unique uppercase FIGIs')
    if args.acquire_sec_missing or args.sec_runtime_root_ch is not None:
        if args.command not in ('prepare-sources','launch','launch-collection') or args.fundamental_start_date is None:
            p.error('SEC acquisition options require a fundamental context bound on source preparation or fresh launch')
    if args.identity_point or args.acquire_identity_missing:
        if args.command not in ('prepare-sources','launch','launch-collection'):
            p.error('Identity preparation belongs to source preparation or fresh launch')
        points=[(r['as_of'],r['ticker']) for r in args.identity_point]
        if not points or len(points)>1000 or len(points)!=len(set(points)):
            p.error('Identity preparation requires 1 to 1000 unique ticker/date points')
        if any(not date(2019,1,2)<=date.fromisoformat(r['as_of'])<=args.universe_anchor_date for r in args.identity_point):
            p.error('Identity observations must be within canonical history and the universe anchor date')
    if args.staging_reserve_gib<0 or args.staging_chunk_gib<0 or not 0<=args.staging_reserve_fraction<1:
        p.error('Invalid SSD staging reserve or chunk bound')
    if args.recover_retained_reporting and args.command not in ('prepare-sources','launch','launch-collection'):
        p.error('--recover-retained-reporting belongs to prepare-sources')
    try:contracts.identifier(args.database)
    except ValueError as error:p.error(str(error))
    if args.command!='schema' and args.format=='sql':p.error('SQL format is for schema inspection only')
    if args.command in ('status','stored-plan','verify','measure-storage','estimate-storage','launch','launch-collection','backfill-v7','run','resume') and args.dataset_id is None:p.error(args.command+' requires --dataset-id')
    if args.collection and args.command!='estimate-storage':p.error('--collection belongs to estimate-storage')
    if args.command=='estimate-storage' and (args.acquire_missing or args.stage_archive):p.error('Storage estimation cannot acquire sources')
    if args.command in ('launch','launch-collection','backfill-v7','run','resume') and args.capacity_profile is None:p.error(args.command+' requires --capacity-profile')
    if args.command=='backfill-v7':
        if args.source_dataset_id is None or args.source_dataset_id==args.dataset_id:
            p.error('backfill-v7 requires distinct source and new target dataset UUIDs')
        if args.acquire_missing or args.stage_archive or args.recover_retained_reporting:
            p.error('backfill-v7 reads certified ClickHouse inputs only; prepare sources before backfill')
    elif any(v is not None for v in (args.source_dataset_id,args.backfill_start_date,args.backfill_end_date)):
        p.error('Source dataset and backfill bounds belong to backfill-v7')
    if args.command in ('launch','launch-collection') and any(v is None for v in (args.context_start_date,args.fundamental_start_date)):
        p.error('launch requires --context-start-date and --fundamental-start-date')
    if args.rebuild_origin_date is not None and args.command not in ('launch','launch-collection'):
        p.error('--rebuild-origin-date belongs to fresh launch; resume uses the frozen plan')
    if args.command=='prepare-sources' and (args.end_date is None or args.format!='text'):
        p.error('prepare-sources requires --end-date and uses text progress')
    if args.command=='qualify-sec-lineage' and (args.end_date is None or args.fundamental_start_date is None):
        p.error('qualify-sec-lineage requires --fundamental-start-date and --end-date')
    if args.command=='qualify-sec-lineage' and (args.acquire_missing or args.stage_archive):
        p.error('SEC lineage qualification does not acquire market files')
    if args.command=='plan-cohorts':
        if args.end_date is None or args.context_start_date is None or args.issuer_keys:
            p.error('plan-cohorts requires context/end bounds and an unscoped universe')
        if not 1<=args.cohort_max_issuers<=256 or not 1<=args.cohort_max_sessions<=250000:
            p.error('Invalid bounded cohort limits')
        if args.acquire_missing or args.stage_archive:p.error('plan-cohorts is read-only')
    if args.command=='launch-collection' and (args.issuer_keys or not 1<=args.cohort_max_issuers<=256 or not 1<=args.cohort_max_sessions<=250000):
        p.error('launch-collection requires an unscoped universe and bounded cohort limits')
    if args.hydrate_missing_trades:p.error('Imported-day reporting repair cannot hydrate flatfiles; G-to-D staging belongs to acquisition')
    if args.command in ('verify','measure-storage') and (args.acquire_missing or args.stage_archive):p.error('Verification/measurement is read-only; acquisition flags belong to prepare-sources')
    if args.measurement_tables is not None and args.command!='measure-storage':p.error('--measurement-tables belongs to measure-storage')
    if args.observe_storage and args.command not in ('run','resume'):p.error('--observe-storage belongs to bounded stored run/resume pilots')
    if args.command in ('run','resume') and (args.acquire_missing or args.stage_archive):p.error('Frozen execution cannot acquire sources; use prepare-sources before freezing the plan')
    if args.command in ('resume-collection','verify-collection'):
        if args.dataset_id is None:p.error('Collection execution requires --dataset-id (collection UUID)')
        if args.command=='resume-collection' and args.capacity_profile is None:p.error('resume-collection requires --capacity-profile')
        if args.acquire_missing or args.stage_archive or args.recover_retained_reporting:
            p.error('Frozen collection execution cannot acquire or repair sources')
    if args.publish_conid_mappings and args.command not in ('reconcile-conids','review-conid-issues'):
        p.error('--publish-conid-mappings requires reconcile-conids or review-conid-issues')
    if (args.case_details or args.case_ticker) and args.command!='review-conid-issues':
        p.error('Per-case display options require review-conid-issues')
    if len(args.case_ticker)>128:p.error('At most 128 display ticker filters are allowed')
    if args.command in ('reconcile-conids','review-conid-issues') and (args.acquire_missing or args.stage_archive or args.repair_retained_symbols):
        p.error('Conid reconciliation is separate from acquisition and dated-symbol publication')
    return args


def campaign_config(args,end):
    """One authority for launch selections and requirement-derived context."""
    return contracts.CampaignConfig(args.start_date,end,database=args.database,workers=args.workers,
        query_threads=args.query_threads,query_memory_bytes=args.query_memory_gib*1024**3,
        global_memory_bytes=args.global_memory_gib*1024**3,volume_windows_seconds=args.volume_windows_seconds,
        short_windows_sessions=args.short_windows_sessions,resolutions_ms=args.resolutions_ms,issuer_keys=args.issuer_keys,
        conid_review_hash=args.conid_review_hash,query_timeout_seconds=args.query_timeout_seconds)


def prepare_identity(args,claim,client_factory,dictionary_factory,stop,progress=None):
    """Prepare explicit reference points before freezing any derived graph."""
    if not args.identity_point:return None
    from pipelines.certified_research import historical_identity
    def provider_factory():
        # Construct only inside an opted-in missing-point producer. Do not probe
        # credentials or the provider during normal reuse-only preparation.
        from services.reference_gateway.config import ReferenceGatewayConfig
        from services.reference_gateway.providers import MassiveReferenceClient
        config=ReferenceGatewayConfig.from_env()
        return MassiveReferenceClient(base_url=config.massive_base_url,
            api_key=os.environ.get('MASSIVE_API_KEY',''),page_limit=config.active_ticker_page_limit,
            max_pages=config.active_ticker_max_pages)
    result=historical_identity.prepare_observations(args.database,claim,client_factory,provider_factory,
        dictionary_factory,claim.check,args.identity_point,acquire_missing=args.acquire_identity_missing,
        workers=args.workers,stop=stop,progress=progress)
    if result['status']=='interrupted':raise InterruptedError('Historical identity preparation interrupted')
    if result['status']!='complete':
        failures=','.join(sorted(result['errors']))
        raise ValueError('Historical identity prerequisites did not certify: '+failures+
            '; missing points require explicit acquisition after resubscription; conflicts require identity resolution')
    if args.format=='text':
        from collections import Counter
        counts=Counter(result['units'].values())
        print(f'Identity points | certified={counts["certified"]} | reused={counts["reused"]} | failed=0',flush=True)
    return result


def prepare_sec_share_sources(args,client,config,claim,client_factory,dictionary_factory,stop,progress=None):
    census=None
    submission_census=None
    reconciliation=None
    submission_coverage=None
    if args.sec_census_cik:
        from pipelines.certified_research import sec_filing_census
        claim.check()
        census=sec_filing_census.inventory(client.query,args.sec_census_cik,args.fundamental_start_date,config.end,
            forms=args.sec_census_forms,maximum_documents=1000)
        claim.check()
        if not census['discovery_ready'] or not census['documents']:
            raise ValueError('Retained SEC filing discovery is not ready: '+str(census['issue_counts']))
        if args.reconcile_sec_submissions:
            from pipelines.certified_research import sec_submission_coverage
            submission_census=sec_filing_census.inventory(client.query,args.sec_census_cik,args.fundamental_start_date,config.end,
                forms=args.sec_census_forms,maximum_documents=1000,source='bulk_submissions')
            claim.check()
            reconciliation=sec_filing_census.reconcile(census,submission_census)
            submission_coverage=sec_submission_coverage.audit(client.query,submission_census)
            claim.check()
            if not reconciliation['identity_reconciled'] or not submission_coverage['retained_evidence_ready']:
                raise ValueError('SEC filing identities differ or retained submission coverage is incomplete: '+
                    json.dumps(dict(reconciliation=reconciliation,coverage=submission_coverage),sort_keys=True,default=str))
        args.sec_share_document=[(row['cik'],row['accession'],row['primary_document']) for row in census['documents']]
    if not args.sec_share_document:return None
    from pipelines.certified_research import sec_share_preparation,capacity_profile
    result=sec_share_preparation.prepare(client.query,config,args.sec_share_document,claim,client_factory,dictionary_factory,
        capacity_profile.load(args.capacity_profile),stop=stop,progress=progress,retain_descriptors=args.verify_sec_archives)
    if census is not None:
        result['filing_census']=dict(census_hash=census['census_hash'],source_hash=census['source_hash'],filings=census['filings'],coverage_certified=False)
        if reconciliation is not None:result['filing_census']['submission_reconciliation']=reconciliation
        if submission_coverage is not None:result['filing_census']['submission_coverage']=submission_coverage
    if result['status']=='interrupted':raise InterruptedError('SEC candidate source preparation interrupted')
    if result['status']!='complete':
        details='; '.join(f'{key}: {client.clean_error(error)[:200]}' for key,error in list(result['errors'].items())[:8])
        raise ValueError('SEC candidate source preparation failed: '+details)
    if args.format=='text':print(f'SEC share source candidates | captured={result["captured"]} | reused={result["reused"]} | allocation certified=false',flush=True)
    if args.verify_sec_archives:
        from pipelines.certified_research import sec_archive_preparation
        descriptors=tuple(result['results'][str(index)].pop('archive_descriptor') for index in range(len(args.sec_share_document)))
        def archive_progress(event):
            if args.format=='text':print(f'SEC archive | {event["phase"]} | {event["archive"]}',flush=True)
        result['archive_evidence']=sec_archive_preparation.prepare(client.query,config,args.sec_share_document,claim,
            dictionary_factory,capacity_profile.load(args.capacity_profile),
            canonical_root=args.sec_archive_canonical_root,backup_root=args.sec_archive_backup_root,
            cache_root=args.runtime_root/'sec-archive-staging',runtime_root=args.runtime_root,
            receipt_ledger=args.sec_download_receipts,mode=args.sec_public_time_mode,stop=stop,
            reserve_bytes=args.staging_reserve_gib*1024**3,reserve_fraction=args.staging_reserve_fraction,
            maximum_chunk_bytes=args.staging_chunk_gib*1024**3,progress=archive_progress,prepared_descriptors=descriptors)
        if args.prepare_sec_fundamental_evidence:
            from pipelines.certified_research import sec_fundamental_preparation
            def fundamental_progress(event):
                if args.format=='text':print(f'SEC fundamental evidence | verified {event["completed"]}/{event["total"]} documents | inserted={event["inserted_rows"]} | parity inputs ready={event["parity_inputs_ready"]}',flush=True)
            result['fundamental_evidence']=sec_fundamental_preparation.prepare(client.query,config,args.sec_share_document,claim,
                dictionary_factory,capacity_profile.load(args.capacity_profile),mode=args.sec_public_time_mode,stop=stop,
                progress=fundamental_progress,numeric_mode=args.sec_numeric_mode)
            if args.format=='text':
                evidence=result['fundamental_evidence']
                print('SEC parity input blockers | '+json.dumps(evidence.get('parity_input_blockers',{}),sort_keys=True)+' | coverage certified=false',flush=True)
    if census is not None:
        claim.check()
        after=sec_filing_census.inventory(client.query,args.sec_census_cik,args.fundamental_start_date,config.end,
            forms=args.sec_census_forms,maximum_documents=1000)
        claim.check()
        if after['census_hash']!=census['census_hash']:
            raise ValueError('Retained SEC filing census changed during source preparation; prepared evidence retained')
        if submission_census is not None:
            submission_after=sec_filing_census.inventory(client.query,args.sec_census_cik,args.fundamental_start_date,config.end,
                forms=args.sec_census_forms,maximum_documents=1000,source='bulk_submissions')
            claim.check()
            if submission_after['census_hash']!=submission_census['census_hash']:
                raise ValueError('Independent SEC submission census changed during source preparation; prepared evidence retained')
            if sec_submission_coverage.audit(client.query,submission_after)['audit_hash']!=submission_coverage['audit_hash']:
                raise ValueError('Independent SEC submission coverage changed during source preparation; prepared evidence retained')
            claim.check()
    return result


def prepare_retained_symbols(args,client,claim,dictionary_factory,stop,through,*,context_start=None):
    if not args.repair_retained_symbols:return None
    from services.reference_gateway.retained_symbol_repair import rebuild
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('Retained symbol preparation interrupted')
    guard()
    dictionary=dictionary_factory()
    try:
        if dictionary.dictionary_database!='q_live':raise ValueError('Retained symbol repair requires Gateway dictionary ownership')
        from pipelines.certified_research.historical_identity import load_origins
        context_start=min(value for value in (context_start,args.context_start_date,args.rebuild_origin_date,args.start_date) if value is not None)
        result=rebuild(client.http,database='q_live',identifiers=args.retained_figi,
            context_start=context_start,claim=dictionary,execute=True,guard=guard,through=through,
            origin_loader=lambda:load_origins(client.query,args.database,args.identity_point,
                context_start=context_start,figis=set(args.retained_figi)))
    finally:dictionary.close()
    if args.format=='text':print(f'Retained symbols | intervals={result["intervals"]} | inserted={result["inserted"]}',flush=True)
    return result


def inspect_sources(query,args):
    status=schema.verify_storage(query,args.database)
    source_policy=query("SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name "
                        "WHERE policy_name='sip_raw_ssd'")
    if not source_policy or any(r['disk_name']!='sip_raw_ssd' for r in source_policy):
        raise ValueError('Canonical source policy is absent or incorrectly routed')
    source_parts=query("SELECT table,disk_name FROM system.parts WHERE active AND database='market_sip_compact' "
                       "AND match(table,'^events_[0-9]{4}$') GROUP BY table,disk_name")
    if not source_parts or any(r['disk_name']!='sip_raw_ssd' for r in source_parts):
        raise ValueError('Canonical source parts are missing or incorrectly placed')
    source=query("SELECT min(source_date) first_day,max(source_date) last_day,count() sessions,"
                 "sum(total_event_rows_after_filters) events,sum(trade_event_rows) trades "
                 "FROM market_sip_compact.events_source_day_stats FINAL "
                 f"WHERE source_date>={contracts.literal(args.start_date)}"+
                 (f" AND source_date<={contracts.literal(args.end_date)}" if args.end_date else ''))
    if len(source)!=1 or not source[0]['sessions']:
        raise ValueError('No certified source-day statistics; acquisition is required')
    end=args.end_date or date.fromisoformat(source[0]['last_day'])
    config=campaign_config(args,end)
    mutations=query("SELECT database,table,mutation_id FROM system.mutations "
                    "WHERE database='market_sip_compact' AND startsWith(table,'events_') AND NOT is_done LIMIT 20")
    return dict(status='blocked' if mutations else 'inspected',source=source[0],storage=status,
                config=config.identity(),plan_hash=config.content_hash,canonical_mutations=len(mutations),
                campaign_execution_enabled=True,
                reason='Source/storage inspection only; launch admission remains independently required')


def qualify_plan(result):
    """Inspection evidence cannot masquerade as campaign admission."""
    blocked=[];inputs=result['source_plan'];membership=result['universe_preflight']
    for field in ('missing_sources','incomplete_sources','missing_reporting'):
        if inputs[field]:blocked.append(f'{field}: {len(inputs[field])} sessions')
    if result['canonical_mutations']:blocked.append('canonical source mutations are active')
    if not membership['ready']:blocked.append(f"historical universe: {membership['rejected']} unresolved identity/interval records")
    for family,summary in result['short_publication_preflight']['families'].items():
        if summary['repair_required']:blocked.append(f"{family}: {summary['unknown_public_time']} rows lack public-time evidence")
    if not result['corporate_evidence_preflight']['original_public_vintage_certified']:
        blocked.append('corporate actions: original public vintages are not certified')
    if 'sec_acquisition_preflight' in result and not result['sec_acquisition_preflight']['ready']:
        blocked.append('SEC acquisition: incomplete '+','.join(result['sec_acquisition_preflight']['gaps']))
    if result['campaign_execution_enabled'] and not result.get('batch_conid_pool'):
        blocked.append('batch universe: select an explicit sealed conid review')
    if 'storage' in result and not result['storage']['tables']:blocked.append('research output schemas are not installed')
    if not result['campaign_execution_enabled']:blocked.append('campaign execution handlers are unavailable')
    result['execution_admission_checked']=False
    result['admission_blockers']=blocked;result['status']='blocked' if blocked else 'ready'
    result['reason']='; '.join(blocked) if blocked else 'Data prerequisites inspected; independent launch admission required'
    return result


def main(argv=None):
    args=parse_args(argv)
    if args.command=='schema':
        catalog=installation.catalog(args.database)
        from pipelines.certified_research import sec_share_capture,sec_share_preparation,sec_witness_publication,sec_archive_preparation,sec_fundamental_publication,sec_fundamental_preparation,sec_reported_publication,sec_scope_specification,sec_scope_preparation
        source_tables=sec_share_capture.tables()
        archive_tables=sec_witness_publication.tables()
        fundamental_tables=sec_fundamental_publication.tables()
        reported_tables=sec_reported_publication.tables()
        scope_tables=sec_scope_specification.tables()
        from pipelines.certified_research import conid_reconciliation,conid_case_review
        conid_tables=(conid_reconciliation.table(),conid_reconciliation.receipt_table(),conid_case_review.table(),conid_case_review.receipt_table(),conid_case_review.evidence_table())
        if args.format=='sql':print(';\n\n'.join([*(t.ddl(args.database) for _,t in catalog),
            *(t.ddl('q_live') for t in (*source_tables,*archive_tables,*fundamental_tables,*reported_tables,*scope_tables,*conid_tables))])+';')
        elif args.format=='json':
            manifest=installation.manifest(args.database)
            manifest['conid_reconciliation']=dict(database='q_live',version=conid_reconciliation.VERSION,
                broker_execution_certified=False,historical_membership_certified=False,
                tables=[dict(name=t.name,engine='ReplacingMergeTree' if isinstance(t,conid_case_review.ReviewTable) else 'MergeTree',
                    order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in conid_tables])
            manifest['sec_source_candidates']=dict(database='q_live',product=sec_share_preparation.PRODUCT,
                recipe_hash=sec_share_preparation.recipe_hash(),candidate_only=True,
                tables=[dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in source_tables])
            manifest['sec_archive_evidence']=dict(database='q_live',product=sec_archive_preparation.PRODUCT,
                recipe_hash=sec_archive_preparation.recipe_hash(),allocation_certified=False,
                tables=[dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in archive_tables])
            manifest['sec_fundamental_evidence']=dict(database='q_live',certified=False,product=sec_fundamental_preparation.PRODUCT,
                recipe_hash=sec_fundamental_preparation.recipe_hash(),
                tables=[dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in fundamental_tables])
            manifest['sec_reported_projection_candidates']=dict(database='q_live',certified=False,
                product='sec_reported_projection',mode_recipes={mode:sec_fundamental_preparation.reported_recipe_hash(mode) for mode in ('round-to-nearest','truncation')},
                tables=[dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in reported_tables])
            manifest['sec_original_scope_specifications']=dict(database='q_live',certified=False,product=sec_scope_preparation.PRODUCT,
                recipe_hash=sec_scope_preparation.recipe_hash(),default_forms=sec_scope_preparation.DEFAULT_FORMS,
                tables=[dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns]) for t in scope_tables])
            print(json.dumps(manifest,indent=2))
        else:
            print(f'Compact research schema | {len(catalog)} tables | live_market_ssd')
            for group,t in catalog:print(f'  {group}: {t.name} | {len(t.columns)} columns, explicit lossless codecs')
            print(f'Optional SEC source candidates | {len(source_tables)} q_live tables | explicit document selection and measured admission required')
            print('Use --format sql for exact DDL or --format json for the column catalog.')
            print('Optional conid reconciliation/review | 5 q_live tables | --publish-conid-mappings')
        return 0
    from scripts import build_market_day as builder
    args.max_threads=args.query_threads;args.max_memory_gb=args.query_memory_gib;args.query_timeout=args.query_timeout_seconds
    client=builder.Client(args,persistent=False,readonly=args.command in
        ('preflight','plan','plan-cohorts','status','stored-plan','verify','verify-collection','measure-storage','measure-sec-extraction','estimate-storage','audit-sec-submissions') or
        (args.command in ('reconcile-conids','review-conid-issues') and not args.publish_conid_mappings))
    try:
        if args.command in ('reconcile-conids','review-conid-issues'):
            from pipelines.certified_research import conid_reconciliation as reconciliation
            end=args.end_date or args.universe_anchor_date
            if args.format=='text':print('Conid reconciliation | active=retained metadata | provider acquisition=off',flush=True)
            inputs=reconciliation.load(client.query,args.start_date,end)
            result=reconciliation.reconcile(inputs,args.start_date,end)
            if args.command=='review-conid-issues':
                from pipelines.certified_research import conid_case_review as review
                legacy_inputs=dict(inputs,entities=[dict(row,ticker=row.get('legacy_ticker',row['ticker'])) for row in inputs['entities']])
                legacy_result=reconciliation.reconcile(legacy_inputs,args.start_date,end)
                evidence=review.load(client.query,legacy_result,inputs)
                result=review.review(legacy_result,inputs,evidence)
                if args.publish_conid_mappings:
                    def review_guard():
                        fresh_inputs=reconciliation.load(client.query,args.start_date,end)
                        legacy_inputs=dict(fresh_inputs,entities=[dict(row,ticker=row.get('legacy_ticker',row['ticker'])) for row in fresh_inputs['entities']])
                        base=reconciliation.reconcile(legacy_inputs,args.start_date,end)
                        fresh=review.review(base,fresh_inputs,review.load(client.query,base,fresh_inputs))
                        if fresh['review_hash']!=result['review_hash']:raise ValueError('Per-case review sources changed; repeat review')
                    result['publication']=review.publish(client.query,result,review_guard)
                displayed=result['rows'] if not args.case_ticker else [r for r in result['rows'] if r['ticker'] in set(args.case_ticker)]
                if args.format=='json':print(json.dumps(dict({k:v for k,v in result.items() if k!='proofs'},rows=displayed,shown_rows=len(displayed)),indent=2))
                else:
                    s=result['summary']
                    print(f"Conid case review | reviewed={s['reviewed']} | identity resolved={s['identity_resolved']} | blocked={s['blocked']}")
                    for reason,count in sorted(s['blocked_reasons'].items()):print(f'  {reason}: {count}')
                    if args.case_details or args.case_ticker:
                        for row in displayed:
                            print(f"{row['ticker']} | {row['decision']} | conid={row['conid']} | {row['reason'] or row['method']} | remaining={','.join(row['remaining_issues'])}")
                    print('Canonical issues and historical timelines retain separate certification requirements.')
                    if 'publication' in result:print('ClickHouse case publication: '+json.dumps(result['publication']))
                return 2 if result['summary']['blocked'] else 0
            if args.publish_conid_mappings:
                if args.format=='text':print('Conid reconciliation | active=ownership and exact publication',flush=True)
                from contextlib import closing
                from pipelines.certified_research.publication import DictionaryClaim
                from src.trading_runtime.keeper_session import open_workstation_keeper_session
                from pipelines.market_sip.flatfiles.download_update_events import load_env_files
                load_env_files([args.env_file],verbose=False)
                def source_guard():
                    fresh=reconciliation.reconcile(reconciliation.load(client.query,args.start_date,end),args.start_date,end)
                    if fresh['snapshot_hash']!=result['snapshot_hash']:
                        raise ValueError('Reference sources changed during reconciliation; retry a new snapshot')
                with closing(open_workstation_keeper_session()) as keeper:
                    with closing(DictionaryClaim(keeper.client,'q_live')) as claim:
                        result['publication']=reconciliation.publish(client.query,'q_live',result,claim,source_guard)
            output={k:v for k,v in result.items() if k!='rows'}
            if args.format=='json':print(json.dumps(output,indent=2))
            else:
                summary=result['summary']
                print(f"Conid reconciliation | completed={summary['entities']:,} | resolved={summary['resolved']:,} | blocked={summary['blocked']:,}")
                for reason,count in summary['reasons'].items():print(f'  {reason}: {count:,}')
                print('Resolved identity only; broker execution and dated membership require their own evidence.')
                print('Snapshot: '+result['snapshot_hash'])
                if 'publication' in result:print('ClickHouse publication: '+json.dumps(result['publication']))
            return 2 if result['summary']['blocked'] else 0
        if args.command=='audit-sec-submissions':
            from pipelines.certified_research import sec_submission_coverage
            result=sec_submission_coverage.inspect(client.query,args.sec_census_cik,args.fundamental_start_date,args.end_date,
                forms=args.sec_census_forms)
            if args.format=='json':print(json.dumps(result,indent=2,default=str))
            else:
                check=result['reconciliation']
                print(f'SEC prerequisites | {result["status"]} | retained={check["retained_filings"]} | submissions={check["submission_filings"]} | certified=false')
                for key in ('missing_retained','unconfirmed_retained','conflicting_metadata'):
                    for cik,accession in check[key]:print(f'  {key}: CIK {cik}, accession {accession}')
                for issue in result['coverage']['issues']:print('  '+json.dumps(issue,sort_keys=True,default=str))
                print(result['next_requirement'])
            return 2 if result['status']=='blocked' else 0
        if args.command=='measure-sec-extraction':
            from pipelines.certified_research import sec_extraction_measurement
            result=sec_extraction_measurement.measure(client.query,args.measurement_sec_document,compare_bulk=args.compare_sec_bulk)
            if args.format=='json':print(json.dumps(result,indent=2))
            else:
                for row in result['documents']:
                    print(f'SEC extraction | {row["cik"]} | bytes={row["source_bytes"]:,} | observations={row["observations"]} | rejected={row["rejections"]} | fetch={row["fetch_seconds"]:.3f}s | parse={row["parse_seconds"]:.3f}s')
                    if args.compare_sec_bulk:print('  Value comparison | '+json.dumps(row['comparison_status_counts'],sort_keys=True)+f' | unmatched originals={row["unmatched_original_facts"]}')
                    if args.compare_sec_bulk:print('  Unmatched contexts | '+json.dumps(row['unmatched_original_context_counts'],sort_keys=True))
                print('Observed only | no writes | storage and peak rates remain unqualified')
            return 0
        if args.command=='estimate-storage':
            from pipelines.certified_research import storage_estimate,capacity_profile
            profile=capacity_profile.load(args.capacity_profile) if args.capacity_profile is not None else None
            result=storage_estimate.estimate(client.query,args.database,str(args.dataset_id),profile=profile,is_collection=args.collection)
            if args.format=='json':print(json.dumps(result,indent=2,default=str))
            else:
                print('Storage estimate | '+result['status']+' | execution not started')
                if result['status']=='measurement_required':print('Required bytes unknown: compatible measured product rates and peak bounds are missing.')
                else:
                    for name,row in result.get('headroom',{}).items():
                        print(f'{name} | required={row["required_bytes"]:,} bytes | reserved available={row["available_bytes"]:,} bytes')
                    for reason in result.get('blockers',[]):print('Blocked: '+reason)
            return 0 if result['status']=='admitted' else 2
        if args.command in ('resume-collection','verify-collection'):
            from contextlib import closing
            from types import SimpleNamespace
            from threading import Event
            import signal
            import time
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
            from pipelines.certified_research import collection_execution,capacity_profile
            load_env_files([args.env_file],verbose=False)
            verify_only=args.command=='verify-collection'
            profile=None if verify_only else capacity_profile.load(args.capacity_profile)
            stop=Event();original=signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT,lambda *unused:stop.set())
            def factory(config):
                options=SimpleNamespace(env_file=args.env_file,max_threads=config.query_threads,
                    max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=config.query_timeout_seconds)
                real=builder.Client(options,persistent=False,readonly=verify_only)
                if not verify_only:return real
                class Reader:
                    def query(self,sql):return real.query(sql)
                    def close(self):real.close()
                return Reader()
            last_progress=[0.]
            def progress(change):
                now=time.monotonic()
                if args.format=='text' and now-last_progress[0]>=1:
                    print(change.get('phase','execution')+' | '+
                        ' | '.join(f'{k}={v}' for k,v in change.get('counts',{}).items()),flush=True)
                    last_progress[0]=now
            try:
                with closing(open_workstation_keeper_session()) as keeper:
                    claim=KeeperClaim(keeper.client,str(args.dataset_id))
                    try:
                        query=client.query
                        result=collection_execution.execute(query,args.database,str(args.dataset_id),claim,
                            lambda id_:KeeperClaim(keeper.client,id_),factory,
                            lambda:DictionaryClaim(keeper.client,args.database),profile=profile,
                            verify_only=verify_only,stop=stop,progress=progress)
                    finally:claim.close()
            finally:signal.signal(signal.SIGINT,original)
            print(json.dumps(result,default=str) if args.format=='json' else
                'Collection '+result['status']+' | '+str(result.get('certificate',{}).get('root_hash','unsealed')))
            return 0 if result['status'] in ('complete','verified') else 130 if result['status']=='interrupted' else 2
        if args.command in ('launch','launch-collection','backfill-v7','run','resume'):
            from contextlib import closing
            from types import SimpleNamespace
            from threading import Event
            import signal
            import time
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim,SourceClaim
            from pipelines.certified_research import saved_execution,capacity_profile,fresh_execution,checkpoint_backfill
            profile=capacity_profile.load(args.capacity_profile)
            load_env_files([args.env_file],verbose=False)
            stop=Event();last=[0.];counts={}
            def progress(change):
                counts.clear();counts.update(change['counts'])
                now=time.monotonic()
                if args.format=='text' and now-last[0]>=1:
                    print(change.get('phase','execution')+' | '+' | '.join(f'{k}={v}' for k,v in sorted(counts.items())),flush=True)
                    last[0]=now
            def factory(config):
                options=SimpleNamespace(env_file=args.env_file,max_threads=config.query_threads,
                    max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=config.query_timeout_seconds)
                return builder.Client(options,persistent=False)
            saved=None if args.command in ('launch','launch-collection') else campaign.load_plan(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database,
                str(args.source_dataset_id if args.command=='backfill-v7' else args.dataset_id))
            if saved is None:
                end=args.end_date
                if end is None:
                    frontier=client.query('SELECT max(source_date) last_day FROM market_sip_compact.events_source_day_stats FINAL')[0]['last_day']
                    if not frontier or str(frontier)=='1970-01-01':raise ValueError('No canonical source frontier; select an explicit acquisition end date')
                    end=date.fromisoformat(str(frontier))
                config=campaign_config(args,end)
            else:config=saved.config
            source_context=args.context_start_date
            if args.command in ('launch','launch-collection'):
                from pipelines.certified_research.context import preparation_start
                source_context=preparation_start(config,source_context,rebuild_origin=args.rebuild_origin_date)
            observations=None
            if args.observe_storage:
                from pipelines.certified_research import capacity_measurement
                if len(saved.units)>64:raise ValueError('Storage observation pilot exceeds the 64-unit execution bound')
                catalog=installation.catalog(args.database)
                staging=tuple(t.name for group,t in catalog if group=='staging')
                outputs=tuple(t.name for group,t in catalog if group=='published' and any(c.name=='dataset_id' for c in t.columns))
                capacity_measurement.require_isolated(client.query,args.database,str(args.dataset_id),(*staging,*outputs))
                def snapshot(query):
                    capacity_measurement.require_isolated(query,args.database,str(args.dataset_id),(*staging,*outputs))
                    return capacity_measurement.transient_snapshot(query,args.database,staging,outputs)
                observations=capacity_measurement.PhaseObservations(snapshot)
            inspection_client=client
            client=factory(config)
            inspection_client.close()
            original=signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT,lambda *unused:stop.set())
            try:
                with closing(open_workstation_keeper_session()) as keeper:
                    if args.command in ('launch','launch-collection'):
                        paths=acquisition.IngestionPaths(args.env_file,args.runtime_root,args.cache_root,args.archive_root,args.clickhouse_cache_root)
                        paths.validate()
                        dictionary=DictionaryClaim(keeper.client,args.database)
                        try:installation.install(client.query,args.database,dictionary)
                        finally:dictionary.close()
                        existing=client.query(f'SELECT count() n FROM {args.database}.research_dataset_v1 '
                            f"WHERE dataset_id=toUUID('{args.dataset_id}')")[0]['n']
                        if int(existing):raise ValueError('Dataset already has a frozen definition; use resume')
                        if args.command=='launch-collection':
                            exists=client.query(f"SELECT count() n FROM system.tables WHERE database='{args.database}' AND name='research_collection_v1'")[0]['n']
                            if int(exists) and int(client.query(f"SELECT count() n FROM {args.database}.research_collection_v1 WHERE collection_id=toUUID('{args.dataset_id}')")[0]['n']):
                                raise ValueError('Collection already has a frozen definition; use resume-collection')
                        source_claim=SourceClaim(keeper.client)
                        try:
                            prepare_identity(args,source_claim,lambda:factory(config),
                                lambda:DictionaryClaim(keeper.client,args.database),stop,progress)
                            prepare_retained_symbols(args,client,source_claim,lambda:DictionaryClaim(keeper.client,'q_live'),stop,config.end,
                                context_start=source_context)
                            from pipelines.certified_research import sec_acquisition
                            sec_acquisition.prepare(client.query,paths,config,source_claim,stop,
                                fundamental_start=args.fundamental_start_date,acquire_missing=args.acquire_sec_missing,
                                runtime_root_ch=args.sec_runtime_root_ch,
                                emit=print if args.format=='text' else lambda text:None)
                            sec_candidates=prepare_sec_share_sources(args,client,config,source_claim,lambda:factory(config),
                                lambda:DictionaryClaim(keeper.client,'q_live'),stop,progress)
                            acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,source_claim,
                                stop,acquire_missing=args.acquire_missing,stage_archive=args.stage_archive,context_start=source_context,
                                writer_query=client.query,recover_retained_reporting=args.recover_retained_reporting,
                                staging_reserve_bytes=args.staging_reserve_gib*1024**3,staging_reserve_fraction=args.staging_reserve_fraction,
                                staging_chunk_bytes=args.staging_chunk_gib*1024**3)
                        finally:source_claim.close()
                    claim=KeeperClaim(keeper.client,str(args.dataset_id))
                    try:
                        dictionary_factory=lambda:DictionaryClaim(keeper.client,args.database)
                        def prepare_discovered_sources(days):
                            if args.command not in ('launch','launch-collection'):
                                raise ValueError('Frozen execution cannot prepare newly discovered sources')
                            claim.check()
                            source_owner=SourceClaim(keeper.client)
                            try:
                                acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,source_owner,
                                    stop,context_start=min(days),sessions=days,acquire_missing=args.acquire_missing,
                                    stage_archive=args.stage_archive,writer_query=client.query,
                                    recover_retained_reporting=args.recover_retained_reporting,
                                    staging_reserve_bytes=args.staging_reserve_gib*1024**3,
                                    staging_reserve_fraction=args.staging_reserve_fraction,
                                    staging_chunk_bytes=args.staging_chunk_gib*1024**3)
                            finally:source_owner.close()
                            claim.check()
                        admission=capacity_profile.factory(client.query,profile)
                        def prepare_sec_scopes(query,child_config,plans,owner,guard):
                            from pipelines.certified_research import sec_scope_preparation
                            source_owner=SourceClaim(keeper.client)
                            try:
                                def current():owner.check();guard();source_owner.check()
                                def scope_progress(event):
                                    if args.format=='text':print(f'SEC scopes | {event["state"]} issuer={event["entity_key"]} | completed={event["completed"]}/{event["total"]} | inserted={event["inserted_rows"]}',flush=True)
                                return sec_scope_preparation.prepare(query,child_config,plans,source_owner,
                                    lambda:DictionaryClaim(keeper.client,'q_live'),profile,current,stop=stop,progress=scope_progress,
                                    forms=args.sec_census_forms or sec_scope_preparation.DEFAULT_FORMS,
                                    numeric_mode=args.sec_numeric_mode,public_time_mode=args.sec_public_time_mode)
                            finally:source_owner.close()
                        if args.command=='launch-collection':
                            from pipelines.certified_research import collection_preparation,collection_execution
                            from pipelines.certified_research import sec_scope_preparation
                            prepared=collection_preparation.prepare(client.query,config,str(args.dataset_id),claim,
                                lambda id_:KeeperClaim(keeper.client,id_),factory,dictionary_factory,admission,
                                context_start=source_context,fundamental_start=args.fundamental_start_date,
                                anchor_date=args.universe_anchor_date,maximum_issuers=args.cohort_max_issuers,
                                maximum_sessions=args.cohort_max_sessions,stop=stop,progress=progress,
                                rebuild_origin=args.rebuild_origin_date,source_preparer=prepare_discovered_sources,sec_scope_preparer=prepare_sec_scopes,
                                sec_scope_preparation_hash=sec_scope_preparation.selection_hash(forms=args.sec_census_forms or sec_scope_preparation.DEFAULT_FORMS,
                                    numeric_mode=args.sec_numeric_mode,public_time_mode=args.sec_public_time_mode))
                            result=collection_execution.execute(client.query,args.database,str(args.dataset_id),claim,
                                lambda id_:KeeperClaim(keeper.client,id_),factory,dictionary_factory,
                                profile=profile,stop=stop,progress=progress) if prepared['status']=='prepared' else prepared
                        elif args.command=='launch':
                            result=fresh_execution.run(client.query,config,str(args.dataset_id),claim,factory,
                                dictionary_factory,admission,context_start=source_context,fundamental_start=args.fundamental_start_date,
                                anchor_date=args.universe_anchor_date,stop=stop,progress=progress,
                                rebuild_origin=args.rebuild_origin_date,source_preparer=prepare_discovered_sources,sec_scope_preparer=prepare_sec_scopes)
                        elif args.command=='backfill-v7':
                            result=checkpoint_backfill.run(client.query,args.database,str(args.source_dataset_id),str(args.dataset_id),
                                claim,factory,dictionary_factory,admission,stop=stop,progress=progress,
                                start=args.backfill_start_date,end=args.backfill_end_date)
                        else:result=saved_execution.run(client.query,args.database,str(args.dataset_id),claim,factory,
                            dictionary_factory,admission,stop=stop,progress=progress,
                            **({'storage_observer':observations.record} if observations is not None else {}))
                    finally:claim.close()
            finally:signal.signal(signal.SIGINT,original)
            if args.sec_share_document and args.command=='launch-collection' and sec_candidates is not None:
                result['sec_source_candidates']={k:sec_candidates[k] for k in ('status','captured','reused','candidate_only','allocation_certified','original_vintage_qualified')}
            if args.command=='launch-collection':
                if args.format=='json':print(json.dumps(result,default=str))
                else:
                    print('Collection '+result['status']+' | '+str(result.get('certificate',{}).get('root_hash','unsealed')))
                    for lane,count in result.get('history_dependencies',{}).get('lanes',{}).items():
                        print(f'  Historical identity: {lane}={count}')
                return 0 if result['status']=='complete' else 130 if result['status']=='interrupted' else 2
            if not counts and result['status']=='complete' and result.get('seal'):
                counts=dict(queued=0,active=0,reused=int(result['seal']['units']),failed=0,blocked=0,interrupted=0)
            summary=dict(status=result['status'],counts=counts,seal=result.get('seal'),
                failures={k:client.clean_error(v)[:256] for k,v in list(result['errors'].items())[:20]},
                failed_units=len(result['errors']))
            if args.sec_share_document and args.command=='launch' and sec_candidates is not None:
                summary['sec_source_candidates']={k:sec_candidates[k] for k in ('status','captured','reused','candidate_only','allocation_certified','original_vintage_qualified')}
            if 'measurements' in result:summary['measurements']=result['measurements']
            if 'missing_authorities' in result:
                summary['missing_authorities']=result['missing_authorities']
                from collections import Counter
                summary['missing_authority_counts']=dict(sorted(Counter(
                    row['family'] for row in result['missing_authorities']).items()))
            if observations is not None:
                captured=observations.result()
                summary['storage_observations']=dict(samples=len(captured['samples']),observed_maxima=captured['observed_maxima'],
                    evidence_hash=captured['evidence_hash'],capture_status=captured['capture_status'],
                    execution_status=result['status'],upper_bound_proven=False)
            if args.format=='json':print(json.dumps(summary,indent=2,default=str))
            else:
                print(f"Execution {summary['status']} | failed={summary['failed_units']} | "+
                    (str(summary['seal']['root_hash']) if summary['seal'] else 'unsealed'))
                if 'measurements' in summary:
                    measured=summary['measurements']
                    print(f"  Executor elapsed={measured['elapsed_seconds']:.2f}s | peak active workers={measured['peak_active_workers']} | "
                        f"peak reserved memory={measured['peak_reserved_memory_bytes']/1024**3:.2f} GiB")
                for key,reason in summary['failures'].items():print(f'  {key}: {reason}')
                for family,count in summary.get('missing_authority_counts',{}).items():
                    print(f'  Required reference input: {family} | missing scopes={count} | use --format json for identities and ranges')
            return 0 if result['status']=='complete' else 130 if result['status']=='interrupted' else 2
        if args.command=='qualify-sec-lineage':
            from contextlib import closing
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import SourceClaim
            from pipelines.certified_research import sec_lineage,sec_coverage,planning
            with closing(open_workstation_keeper_session()) as keeper:
                claim=SourceClaim(keeper.client)
                try:
                    result=sec_lineage.qualify(client.query,claim,args.fundamental_start_date,
                        planning.reference_coverage_end(args.end_date))
                finally:claim.close()
            ready=sec_coverage.inventory(client.query,args.fundamental_start_date,planning.reference_coverage_end(args.end_date))
            result['coverage']=ready
            if args.format=='json':print(json.dumps(result,default=str,sort_keys=True))
            else:
                print(f'SEC lineage | qualified={len(result["qualified"])} | unqualified={len(result["unqualified"])} | range_ready={ready["ready"]}')
                if not ready['ready']:print('  Missing range coverage: '+','.join(ready['gaps']))
            return 0 if ready['ready'] else 2
        if args.command in ('verify','measure-storage'):
            from contextlib import closing
            from types import SimpleNamespace
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
            from pipelines.certified_research import verification
            load_env_files([args.env_file],verbose=False)
            def factory(config):
                options=SimpleNamespace(env_file=args.env_file,max_threads=config.query_threads,
                    max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=config.query_timeout_seconds)
                real=builder.Client(options,persistent=False,readonly=True)
                class Reader:
                    def query(self,sql):return real.query(sql)
                    def close(self):real.close()
                return Reader()
            with closing(open_workstation_keeper_session()) as keeper:
                claim=KeeperClaim(keeper.client,str(args.dataset_id))
                try:
                    def verify():
                        return verification.verify(client.query,args.database,
                            str(args.dataset_id),claim,factory,lambda:DictionaryClaim(keeper.client,args.database))
                    if args.command=='measure-storage':
                        from pipelines.certified_research import capacity_measurement
                        tables=args.measurement_tables or ('research_bars_v1','research_indicators_v1','research_indicator_state_v1',
                            'research_liquidity_v1','research_v7_checkpoint_v1','research_activity_v1','research_facts_v1')
                        result=capacity_measurement.measure(client.query,args.database,str(args.dataset_id),tables,
                            lambda:verify()['root_hash'])
                    else:result=verify()
                finally:claim.close()
            if args.format=='json':print(json.dumps(result,indent=2))
            elif args.command=='measure-storage':print(f"Measured persisted output | {sum(t['disk_bytes'] for t in result['tables'])} bytes | projection_ready=false")
            else:print(f"Verified | {result['units']} units | {result['root_hash']} | read-only")
            return 0
        if args.command=='install-schema':
            from contextlib import closing
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import DictionaryClaim
            load_env_files([args.env_file],verbose=False)
            with closing(open_workstation_keeper_session()) as keeper:
                claim=DictionaryClaim(keeper.client,args.database)
                try:result=installation.install(client.query,args.database,claim)
                finally:claim.close()
            if args.format=='json':print(json.dumps(result,indent=2))
            else:print(f"Schema verified | {result['tables']} tables | {result['created']} created | live_market_ssd")
            return 0
        if args.command=='prepare-sources':
            from contextlib import closing
            from threading import Event
            import signal
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import SourceClaim,DictionaryClaim
            load_env_files([args.env_file],verbose=False)
            config=campaign_config(args,args.end_date)
            paths=acquisition.IngestionPaths(args.env_file,args.runtime_root,args.cache_root,args.archive_root,args.clickhouse_cache_root)
            paths.validate()
            stop=Event();original=signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT,lambda *unused:stop.set())
            try:
                with closing(open_workstation_keeper_session()) as keeper:
                    claim=SourceClaim(keeper.client)
                    try:
                        prepare_identity(args,claim,lambda:builder.Client(args,persistent=False),
                            lambda:DictionaryClaim(keeper.client,args.database),stop)
                        prepare_retained_symbols(args,client,claim,lambda:DictionaryClaim(keeper.client,'q_live'),stop,config.end)
                        if args.fundamental_start_date is not None:
                            from pipelines.certified_research import sec_acquisition
                            sec_acquisition.prepare(client.query,paths,config,claim,stop,
                                fundamental_start=args.fundamental_start_date,acquire_missing=args.acquire_sec_missing,
                                runtime_root_ch=args.sec_runtime_root_ch)
                        prepare_sec_share_sources(args,client,config,claim,lambda:builder.Client(args,persistent=False),
                            lambda:DictionaryClaim(keeper.client,'q_live'),stop)
                        result=acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,claim,
                            stop,acquire_missing=args.acquire_missing,stage_archive=args.stage_archive,context_start=args.context_start_date,
                            writer_query=client.query,recover_retained_reporting=args.recover_retained_reporting,
                            staging_reserve_bytes=args.staging_reserve_gib*1024**3,
                            staging_reserve_fraction=args.staging_reserve_fraction,
                            staging_chunk_bytes=args.staging_chunk_gib*1024**3)
                    finally:claim.close()
            finally:signal.signal(signal.SIGINT,original)
            print(f"Source ready | {len(result['source']['expected'])} sessions | {result['source_hash']}")
            print('Derived campaign execution remains under implementation.')
            return 0
        if args.command=='plan-cohorts':
            from pipelines.certified_research import cohorts
            config=campaign_config(args,args.end_date)
            if args.context_start_date>config.start:raise ValueError('Cohort context cannot follow target start')
            query=client.query
            snapshot=universe.load_for_config(query,args.context_start_date,config.end,args.universe_anchor_date,config)
            if not snapshot['ready']:
                result=dict(status='blocked',rejections=snapshot['rejection_counts'],full_source_hash=snapshot['source_hash'],
                    history_dependencies=universe.history_repair_inventory(query,snapshot))
            else:
                result=cohorts.partition_snapshot(snapshot,config,source.exchange_sessions(args.context_start_date,config.end),
                    maximum_issuers=args.cohort_max_issuers,maximum_sessions=args.cohort_max_sessions)
                result.update(status='planned',execution_ready=False,read_only=True)
            print(json.dumps(result,default=str,sort_keys=True,indent=2))
            return 0 if result['status']=='planned' else 2
        if args.command=='status':
            result=campaign.status(client.query,args.database,str(args.dataset_id))
            print(json.dumps(result,indent=2,default=str))
            return 0
        if args.command=='stored-plan':
            from collections import Counter
            saved=campaign.load_plan(client.query,args.database,str(args.dataset_id))
            result=dict(dataset_id=saved.dataset_id,plan_hash=saved.plan_hash,source_hash=saved.source_hash,
                config=saved.config.identity(),units=len(saved.units),products=dict(Counter(p.unit.product for p in saved.units)),
                dependencies=sum(len(p.unit.parents) for p in saved.units),inspection_only=True)
            if args.format=='json':print(json.dumps(result,indent=2,default=str))
            else:print(f"Stored plan verified | {result['units']} units | {result['dependencies']} dependencies | {saved.plan_hash}")
            return 0
        # Every query in the inspection path is read-only at the server as well.
        result=inspect_sources(client.query,args)
        if args.command=='plan':
            result['storage_inventory']=capacity.inventory(client.query,args.database)
            result['corporate_evidence_preflight']=source.corporate_evidence_inventory(
                client.query)
            end=date.fromisoformat(result['config']['config']['end']) if isinstance(result['config']['config']['end'],str) else result['config']['config']['end']
            from pipelines.certified_research import sec_coverage,planning
            result['sec_acquisition_preflight']=sec_coverage.inventory(
                client.query,args.context_start_date or args.start_date,
                planning.reference_coverage_end(end))
            historical=universe.load_for_config(client.query,
                args.context_start_date or args.start_date,end,args.universe_anchor_date,campaign_config(args,end))
            result['universe_preflight']=dict(ready=historical['ready'],intervals=len(historical['intervals']),
                rejected=len(historical['rejected']),rejection_counts=historical['rejection_counts'],
                outside_scope=historical['outside_scope'],source_hash=historical['source_hash'])
            result['universe_repair_requirements']=universe.repair_inventory(historical)
            if 'conid_pool' in historical:result['batch_conid_pool']=historical['conid_pool']
            result['short_publication_preflight']=short_sql.clock_inventory(
                client.query,args.start_date,end)
            inv=source.inventory(client.query,args.start_date,end)
            reporting=source.reporting_inventory(client.query,inv)
            result['source_plan']=dict(missing_sources=[str(d) for d in inv['missing']],
                incomplete_sources=[str(d) for d in inv['incomplete']],
                missing_reporting=[str(d) for d in reporting['missing']],unqualified_reporting=reporting.get('unqualified',{}),source_hash=inv['content_hash'],
                reporting_hash=reporting['content_hash'],acquire_missing=args.acquire_missing,
                hydrate_missing_trades=args.hydrate_missing_trades)
            result['reporting_clock_evidence']=source.clock_evidence_inventory(
                client.query,args.start_date,end)
            qualify_plan(result)
            from pipelines.certified_research import batch_readiness
            result['execution_stages']=batch_readiness.stages(result)
        if args.format=='json':print(json.dumps(result,indent=2,default=str))
        else:
            if args.command=='plan':
                print(batch_readiness.render(result))
                return 2 if result['status']=='blocked' else 0
            summary=result['source']
            print(f"Source inspected | {summary['first_day']} to {summary['last_day']} | {summary['sessions']} sessions")
            print(f"Storage checked | {result['storage']['tables']} existing research tables | {result['storage']['parts']} part groups")
            print(f"Status: {result['status']} | {result['reason']}")
        return 2 if result['status']=='blocked' else 0
    except InterruptedError:
        print('Preparation interrupted; rerun launch before plan publication, or resume an already frozen dataset.',file=sys.stderr)
        return 130
    except Exception as error:
        label='Verification/measurement failed: ' if args.command in ('verify','verify-collection','measure-storage') else 'Execution failed: ' if args.command in ('launch','launch-collection','backfill-v7','run','resume','resume-collection') else 'Preflight failed: '
        print(label+client.clean_error(error),file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Verification interrupted; rerun verify to recheck stored authority.' if args.command=='verify' else
            'Execution interrupted; resume the same immutable dataset after checking status.' if args.command in ('launch','backfill-v7','run','resume') else
            'Source preparation interrupted; verify canonical receipts before resume.',file=sys.stderr)
        return 130
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
