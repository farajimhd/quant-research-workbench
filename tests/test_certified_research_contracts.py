"""Strict classification, exact persistence and causal restore invariants."""
from dataclasses import replace
from datetime import date, datetime
import os
from pathlib import Path
import sys
import unittest
import uuid
from threading import Event,Lock
import time

from pipelines.certified_research import contracts as c, schema as s
from pipelines.certified_research.checkpoints import CheckpointBinding, boundaries, encode, restore
from pipelines.market_sip.events import market_day_sql
from pipelines.certified_research.execution import Unit,execute
from pipelines.certified_research import market,activity
from pipelines.certified_research.publication import Scope,Publisher,KeeperClaim,insert_rows
from pipelines.certified_research.identity import ListingInterval,validate_intervals,identity_key,ensure_identity,ensure_identities,membership_at,LISTING
from pipelines.certified_research import facts
from pipelines.certified_research import source,acquisition
from pipelines.certified_research import v7
from pipelines.certified_research import context
from pipelines.certified_research import producer
from pipelines.certified_research import reference_producer,reference_sql,short_sql,universe,fundamentals,float_data,price_inputs
from pipelines.certified_research.reference_publication import ReferenceScope,ReferencePublisher
from pipelines.certified_research.campaign import Campaign,PlannedUnit,status as campaign_status
from pipelines.certified_research.handlers import ReferenceHandlers,MarketHandlers,UniverseHandlers,ActivityHandlers
from pipelines.certified_research.planning import MarketSession,market_units
from pipelines.certified_research.universe_publication import UniversePublisher


class Contracts(unittest.TestCase):
    def test_universe_repair_inventory_exposes_all_dependencies_without_duplicate_jobs(self):
        row=dict(listing_id='listing:1',security_id='security:1',issuer_id='issuer:1',list_date=None,
            symbol_interval_id='',interval_mapping='',history_status='',history_mapping='',mapping_issue=False)
        report=universe.repair_inventory(dict(rejected=[dict(listing_id='listing:1')],source_rows=[row,row]))
        self.assertEqual(report['counts'],dict(dated_symbol_mapping=1,listing_lifecycle=1,ticker_history_binding=1))
        self.assertTrue(report['inspection_only']);self.assertIn('listing_lifecycle',report['dependencies']['dated_symbol_mapping'])

    def test_plan_marks_unresolved_inputs_blocked_instead_of_inspected(self):
        from scripts.prepare_certified_research import qualify_plan
        result=dict(source_plan=dict(missing_sources=[],incomplete_sources=[],missing_reporting=['2026-01-02']),
            universe_preflight=dict(ready=False,rejected=1),canonical_mutations=0,
            short_publication_preflight=dict(families={'short_volume':dict(repair_required=True,unknown_public_time=3)}),
            corporate_evidence_preflight=dict(original_public_vintage_certified=False),campaign_execution_enabled=False)
        qualified=qualify_plan(result)
        self.assertEqual(qualified['status'],'blocked');self.assertEqual(len(qualified['admission_blockers']),5)

    def test_corporate_preflight_does_not_backdate_current_split_rows(self):
        def query(sql):
            if 'system.columns' in sql:return [dict(name='execution_date',type='Date'),dict(name='inserted_at',type="DateTime64(3,'UTC')")]
            return [dict(disk_name='live_market_ssd',rows='10',compressed_bytes='100')]
        result=source.corporate_evidence_inventory(query)
        self.assertTrue(result['has_execution_date']);self.assertTrue(result['has_local_insertion_time'])
        self.assertFalse(result['original_public_vintage_certified'])

    def test_native_reporting_evidence_builder_has_no_sip_fallback_and_rejects_certified_capture(self):
        from unittest.mock import patch
        from pipelines.market_sip.flatfiles import download_update_events as updater
        with patch.object(sys,'argv',['updater','--start-date','2026-01-02','--end-date','2026-01-02','--skip-bars']):
            args=updater.parse_args()
        root=Path(args.flatfiles_root_win)
        quote=updater.DownloadJob('quotes','2026-01-02','quotes.csv.gz',str(root/'quotes.csv.gz'),1)
        trade=updater.DownloadJob('trades','2026-01-02','trades.csv.gz',str(root/'trades.csv.gz'),1)
        day=updater.DayFiles('2026-01-02',quote,trade)
        sql=updater.raw_event_union_sql(args,day,trade_only=True,include_reporting_reason=True,include_reporting_evidence=True)
        self.assertIn('toUInt64OrNull(participant_timestamp)',sql)
        self.assertIn('reporting_condition_entries',sql)
        self.assertNotIn('AS execution_timestamp_us',sql)
        with patch.object(updater,'latest_day_status',return_value='ok'):
            with self.assertRaisesRegex(RuntimeError,'cannot reopen a certified'):
                updater.capture_reporting_ns(None,args,day,None,Path('unused.jsonl'))

    def test_microsecond_clock_inventory_never_claims_reporting_readiness(self):
        def query(sql):
            if 'system.columns' in sql:return [dict(table='historical_event_execution_clock_v1',name='execution_timestamp_us',type='UInt64')]
            if 'system.parts' in sql:return [dict(table='historical_event_execution_clock_v1',disk_name='live_market_ssd',rows='12',compressed_bytes='100')]
            if 'count() n' in sql:return [dict(n=1)]
            if 'system.tables' in sql:return [dict(name='historical_event_execution_clock_v1',storage_policy='live_market_ssd')]
            return [dict(source_date='2026-01-02',ticker_certificates=1,trades='12',clocks='12')]
        result=source.clock_evidence_inventory(query,date(2026,1,2),date(2026,1,2))
        self.assertFalse(result['reporting_ready']);self.assertEqual(result['parts'][0]['rows'],12)
        self.assertEqual(result['coverage'][0]['table'],'historical_event_execution_clock_coverage_v1')

    def test_activity_context_cannot_omit_an_exchange_session(self):
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4())
        scopes=[Scope(dataset,day,91,'broker_100ms','a'*64) for day in (date(2026,8,21),date(2026,8,25))]
        output=Scope(dataset,date(2026,8,25),91,'activity','b'*64)
        with self.assertRaisesRegex(ValueError,'missing exchange sessions'):
            activity.projection(output,attempt,(10,60),[(scope,dict(attempt_id=attempt)) for scope in scopes])
        with self.assertRaises(ValueError):activity.windows_contract((60,10))

    def test_v7_start_authority_binds_predecessor_and_rejects_mutated_state(self):
        from tests.test_streaming_level_book import engine,bars
        state=engine();authority=v7.StartAuthority.genesis(state,'a'*64)
        self.assertIs(authority.check('a'*64),state)
        with self.assertRaisesRegex(ValueError,'certified predecessor'):authority.check('b'*64)
        state.update(bars(state)[0])
        with self.assertRaisesRegex(ValueError,'mutated'):authority.check('a'*64)
        with self.assertRaisesRegex(ValueError,'untouched empty'):v7.StartAuthority.genesis(state,'a'*64)
        binding=CheckpointBinding('a'*64,'b'*64,'c'*64,'TEST',date(2026,8,21))
        outputs,_=v7.generate(engine(),bars(engine()),binding)
        rolled=v7.StartAuthority.rollover(outputs[-1],date(2026,8,24))
        self.assertEqual(rolled.check(outputs[-1]['state_hash']).session,'2026-08-24')
        with self.assertRaisesRegex(ValueError,'skip required exchange sessions'):
            v7.StartAuthority.rollover(outputs[-1],date(2026,8,25))
        forged=dict(outputs[0],boundary='AH',boundary_us=outputs[-1]['boundary_us'])
        with self.assertRaisesRegex(ValueError,'encoded boundary'):v7.StartAuthority.rollover(forged,date(2026,8,24))

    def test_v7_boundary_payload_does_not_depend_on_later_bars(self):
        from tests.test_streaming_level_book import engine,bars
        a=engine();day=date.fromisoformat(a.session)
        origin=v7.input_origin_hash('a'*64,a.ticker,91,day)
        binding=CheckpointBinding('a'*64,origin,'b'*64,a.ticker,day)
        early=bars(a)
        later=dict(early[-1],t=int(boundaries(day)['REGULAR'].timestamp())+60,open=2.,close=2.,high=2.,low=2.)
        first,_=v7.generate(engine(),early,binding)
        extended,_=v7.generate(engine(),(*early,later),binding)
        self.assertEqual(first[0]['payload'],extended[0]['payload'])
        self.assertEqual(first[1]['payload'],extended[1]['payload'])
        self.assertNotEqual(first[2]['payload'],extended[2]['payload'])

    def test_capacity_inventory_preserves_exact_bytes_and_does_not_fabricate_projection(self):
        from pipelines.certified_research import capacity
        def query(sql):
            if 'system.disks' in sql:return [dict(name='live_market_ssd',total_space='10000',free_space='8000',unreserved_space='7000')]
            return [dict(database='arte',table='bars_v1',rows='10',compressed_bytes='100',uncompressed_bytes='400',disk_bytes='120',parts='1')]
        result=capacity.inventory(query)
        self.assertFalse(result['projection_ready'])
        self.assertEqual(result['tables'][0]['compression_ratio'],4)
        self.assertEqual(result['tables'][0]['compressed_bytes_per_row'],10)
        self.assertEqual(result['disks'][0]['unreserved_space'],7000)

    def test_calculation_selection_is_independent_of_range_and_resource_identity(self):
        original=c.CampaignConfig(date(2026,1,2),date(2026,1,5))
        extended=replace(original,end=date(2026,10,2),workers=2,query_threads=4)
        self.assertNotEqual(original.content_hash,extended.content_hash)
        self.assertEqual(original.calculation_hash,extended.calculation_hash)
        self.assertNotEqual(original.calculation_hash,replace(original,volume_windows_seconds=(10,60,120)).calculation_hash)

    def test_reference_dictionary_claim_covers_only_shared_insert_and_always_releases(self):
        events=[]
        class Claim:
            def check(self):pass
        class Dictionary:
            dictionary_database='arte'
            def check(self):events.append('check')
            def close(self):events.append('close')
        def acquire():events.append('acquire');return Dictionary()
        handlers=ReferenceHandlers(str(uuid.uuid4()),'arte',Claim(),lambda:None,lambda:None,(),dictionary_claim_factory=acquire)
        def query(sql,**kwargs):events.append('query');return 1
        owned=handlers.publication_query(query)
        owned('SELECT 1');self.assertEqual(events,['query']);events.clear()
        owned('INSERT INTO arte.research_provenance_v1 SELECT 1',read=False)
        self.assertEqual(events,['acquire','check','query','check','close']);events.clear()
        def fail(*args,**kwargs):raise RuntimeError('insert uncertain')
        with self.assertRaisesRegex(RuntimeError,'insert uncertain'):
            handlers.publication_query(fail)('INSERT INTO arte.research_provenance_v1 SELECT 1',read=False)
        self.assertEqual(events[-1],'close')
        unsafe=ReferenceHandlers(str(uuid.uuid4()),'arte',Claim(),lambda:None,lambda:None,())
        with self.assertRaisesRegex(ValueError,'global dictionary ownership'):
            unsafe.producer(PlannedUnit(Unit('sec','fundamentals'),{}),{},Event())
        from types import SimpleNamespace
        client=SimpleNamespace(connected=True,client_id=(1,b''),client_state=SimpleNamespace(name='CONNECTED_RO'))
        with self.assertRaisesRegex(RuntimeError,'Keeper is unavailable'):KeeperClaim(client,str(uuid.uuid4()))

    def test_ingestion_ownership_loss_drains_child_and_stop_signals_gracefully(self):
        from unittest.mock import patch
        paths=acquisition.IngestionPaths(Path('fixture.env'))
        command=acquisition.updater_command(paths,date(2026,1,2),date(2026,1,2))
        class Child:
            code=None;terminated=0;signals=0
            def poll(self):return self.code
            def wait(self,**kwargs):return self.code
            def terminate(self):self.terminated+=1;self.code=1
            def send_signal(self,signal):self.signals+=1;self.code=130
        child=Child();checks=[]
        def guard():
            checks.append(1)
            if len(checks)>1:raise RuntimeError('ownership lost')
        with patch.object(acquisition.IngestionPaths,'validate'),patch.object(acquisition.subprocess,'Popen',return_value=child) as spawn:
            with self.assertRaisesRegex(RuntimeError,'ownership lost'):
                acquisition.run_ingestion(command,paths,Event(),guard=guard)
            self.assertEqual(child.terminated,1)
            self.assertEqual(spawn.call_args.kwargs['env']['PYTHONDONTWRITEBYTECODE'],'1')
        child=Child();stop=Event()
        def launch(*args,**kwargs):stop.set();return child
        with patch.object(acquisition.IngestionPaths,'validate'),patch.object(acquisition.subprocess,'Popen',side_effect=launch):
            with self.assertRaises(InterruptedError):acquisition.run_ingestion(command,paths,stop)
        self.assertEqual(child.signals,1);self.assertEqual(child.terminated,0)

    def test_source_preparation_orders_acquisition_repair_and_verified_freeze(self):
        from unittest.mock import patch
        days=(date(2026,1,2),date(2026,1,5));memory=64*1024**2
        config=c.CampaignConfig(*days,query_memory_bytes=memory,global_memory_bytes=4*memory)
        paths=acquisition.IngestionPaths(Path('fixture.env'));commands=[]
        class Claim:
            def check(self):pass
        def query(sql):
            if 'storage_policies' in sql:return [dict(disk_name='sip_raw_ssd' if "='sip_raw_ssd'" in sql else 'live_market_ssd')]
            if 'system.tables' in sql:return [dict(name='events_2026',storage_policy='sip_raw_ssd')]
            if 'system.parts' in sql:return [dict(disk_name='sip_raw_ssd')]
            return [dict(last_day='2025-12-31')]
        def runner(command,paths,stop,**kwargs):commands.append(command);kwargs['guard']()
        missing=dict(expected=days,missing=days,incomplete=());ready=dict(expected=days,missing=(),incomplete=())
        reporting=dict(missing=(days[-1],),mutating=False);complete=dict(missing=(),mutating=False)
        with patch.object(source,'inventory',side_effect=[missing,ready]),\
             patch.object(source,'reporting_inventory',side_effect=[reporting,complete]),\
             patch.object(source,'require_ready',return_value='a'*64) as verify:
            result=acquisition.prepare_sources(query,paths,config,Claim(),Event(),acquire_missing=True,
                stage_archive=True,runner=runner,emit=lambda *args:None)
        self.assertEqual(len(commands),3)
        self.assertIn('download_update_events.py',commands[0][2]);self.assertIn('--archive-root-win',commands[0])
        self.assertEqual(commands[0][commands[0].index('--max-memory-usage')+1],str(memory))
        self.assertEqual(commands[-1][commands[-1].index('--max-memory-gb')+1],'0.0625')
        self.assertIn('backfill_trade_reporting_flags.py',commands[-1][2]);self.assertEqual(result['source_hash'],'a'*64)
        verify.assert_called_once_with(query,ready,complete)
        with patch.object(source,'inventory',return_value=missing):
            with self.assertRaisesRegex(ValueError,'acquisition is not selected'):
                acquisition.prepare_sources(query,paths,config,Claim(),Event(),runner=runner)
        with patch.object(source,'inventory',return_value=missing):
            with self.assertRaisesRegex(ValueError,'did not certify'):
                acquisition.prepare_sources(query,paths,config,Claim(),Event(),acquire_missing=True,runner=runner,emit=lambda *args:None)
        with self.assertRaisesRegex(ValueError,'Source context'):
            acquisition.prepare_sources(query,paths,config,Claim(),Event(),context_start=date(2026,1,3))

    def test_ingestion_archive_copy_is_atomic_contained_and_restart_safe(self):
        from tempfile import TemporaryDirectory
        from hashlib import sha256
        from pipelines.market_sip.flatfiles import archive_staging
        runtime=Path('D:/TradingML/runtimes')
        self.assertTrue(runtime.is_dir(),'Required runtime root is unavailable')
        with TemporaryDirectory(prefix='certified-archive-test-',dir=runtime) as directory:
            root=Path(directory);archive=root/'archive';cache=root/'cache';archive.mkdir();cache.mkdir()
            source_file=archive/'source.gz';data=os.urandom(9*1024**2);source_file.write_bytes(data)
            output=cache/'output.gz';logs=[]
            result=archive_staging.copy_file(source_file,output,archive,cache,len(data),progress=lambda n,total:logs.append(n))
            self.assertEqual(result['sha256'],sha256(data).hexdigest());self.assertEqual(output.read_bytes(),data)
            self.assertEqual(logs[-1],len(data))
            self.assertEqual(archive_staging.copy_file(source_file,output,archive,cache,len(data))['status'],'cached')
            stop=Event()
            with self.assertRaises(InterruptedError):
                archive_staging.copy_file(source_file,cache/'stopped.gz',archive,cache,len(data),stop=stop,
                    progress=lambda *args:stop.set())
            self.assertFalse((cache/'stopped.gz').exists());self.assertFalse(list(cache.glob('*.part')))
            with self.assertRaisesRegex(ValueError,'escapes'):
                archive_staging.copy_file(source_file,root/'outside.gz',archive,cache,len(data))
            self.assertEqual(archive_staging.copy_file(archive/'absent.gz',cache/'absent.gz',archive,cache,1)['status'],'archive_missing')
            with self.assertRaisesRegex(ValueError,'D SSD'):archive_staging.validate_roots(Path('G:/archive'),Path('G:/cache'))
        paths=acquisition.IngestionPaths(Path('fixture.env'))
        command=acquisition.updater_command(paths,date(2026,1,2),date(2026,1,2),stage_archive=True)
        self.assertIn('--archive-root-win',command);self.assertIn(str(paths.archive),command)

    def test_certified_updater_resume_never_reads_flatfiles_and_requires_sidecars(self):
        from unittest.mock import patch
        from pipelines.market_sip.flatfiles import download_update_events as updater
        with patch.object(sys,'argv',['updater','--start-date','2026-01-02','--end-date','2026-01-02','--skip-bars']):
            args=updater.parse_args()
        job=updater.DownloadJob('trades','2026-01-02','unavailable.csv.gz','unavailable.csv.gz',1)
        day=updater.DayFiles('2026-01-02',job,job)
        class Client:
            def query_tsv(self,sql):
                self.sql=sql
                return '3\t2\t5\t10000000000000001\t10000000000000005\t1\t'+updater.source_filter_key(args)+'\n'
        client=Client()
        with patch.object(updater,'event_args_for_day',side_effect=lambda a,d:a),\
             patch.object(updater,'ensure_event_table'),patch.object(updater,'latest_day_status',return_value='ok'),\
             patch.object(updater,'validate_day_event_integrity',return_value={'rows':5}) as validate,\
             patch.object(updater,'rebuild_day_ticker_index'),patch.object(updater,'rebuild_execution_clock_day') as clocks,\
             patch.object(updater,'audit_raw_day_count',side_effect=AssertionError('raw read')),\
             patch.object(updater,'audit_day_events_against_raw_csv',side_effect=AssertionError('raw sample')),\
             patch.object(Path,'stat',side_effect=AssertionError('file access')):
            stats=updater.certified_source_day_stats(client,args,day)
            self.assertEqual(stats.first_sip_timestamp_us,10000000000000001)
            self.assertEqual(updater.run_day(client,args,day,'fixture',Path('unused.jsonl')),'skipped')
            self.assertEqual(validate.call_args.kwargs['expected_event_rows'],5)
            self.assertFalse(clocks.call_args.kwargs['allow_raw_rebuild'])
            args.force_day_rebuild=True
            with self.assertRaisesRegex(RuntimeError,'versioned canonical migration'):
                updater.run_day(client,args,day,'fixture',Path('unused.jsonl'))
        with patch.object(client,'query_tsv',return_value=''):
            with self.assertRaisesRegex(RuntimeError,'missing or ambiguous'):updater.certified_source_day_stats(client,args,day)

    def test_universe_repair_gate_precedes_dictionary_ownership_and_closes_client(self):
        from unittest.mock import patch
        class Claim:
            def check(self):pass
        class Client:
            closed=False
            def query(self,*args):raise AssertionError('Unexpected query')
            def close(self):self.closed=True
        client=Client()
        def dictionary():raise AssertionError('Unresolved source must not acquire a writer')
        handlers=UniverseHandlers(str(uuid.uuid4()),'arte',Claim(),lambda:client,dictionary,lambda:None)
        plan=PlannedUnit(Unit('universe','universe'),dict(context_start='2025-01-01',end='2026-10-02',
            anchor_date='2026-10-12',source_hash='a'*64))
        with patch('pipelines.certified_research.universe.load',return_value=dict(source_hash='a'*64,
                ready=False,rejection_counts={'missing_listing_start':1})):
            with self.assertRaisesRegex(ValueError,'Historical universe requires evidence repair'):
                handlers.producer(plan,{},Event())
        self.assertTrue(client.closed)

    def test_market_plan_parallel_consumers_have_independent_chronological_state(self):
        config=c.CampaignConfig(date(2026,1,2),date(2026,1,5))
        days=(config.start,config.end)
        sessions=[MarketSession(day,1,'TEST') for day in days]
        planned=market_units(config,sessions,source_parents={d:('reporting:'+str(d),) for d in days},
            universe_parent='universe',seed_parents={1:('indicator-seed','v7-seed')})
        first,technical,v7,activity_first,later,later_technical,later_v7,activity_later=(p.unit for p in planned)
        self.assertEqual(first.parents,('reporting:2026-01-02','universe'))
        self.assertEqual(technical.parents,(first.key,'indicator-seed'))
        self.assertEqual(v7.parents,(first.key,'v7-seed'))
        self.assertNotIn(technical.key,later.parents)
        self.assertEqual(later_technical.parents,(later.key,technical.key))
        self.assertEqual(later_v7.parents,(later.key,v7.key))
        self.assertEqual(activity_first.parents,(first.key,))
        self.assertEqual(activity_later.parents,(first.key,later.key))
        with self.assertRaisesRegex(ValueError,'seed prerequisites'):
            market_units(config,sessions,source_parents={d:('source',) for d in days},universe_parent='universe',seed_parents={})
        with self.assertRaisesRegex(ValueError,'Duplicate listing/session'):
            market_units(config,sessions+sessions,source_parents={d:('source',) for d in days},
                universe_parent='universe',seed_parents={1:('indicator-seed','v7-seed')})

    def test_scheduler_large_verified_resume_and_failed_chain_are_linear_and_nonrecursive(self):
        size=10000
        units=tuple(Unit(str(i),'stage',(str(i-1),) if i else (),1) for i in range(size))
        before=time.monotonic();events=[]
        result=execute(units,{'stage':lambda *args:None},workers=2,memory_bytes=2,
            reuse=lambda unit,parents:dict(output_hash='a'*64),progress=events.append)
        self.assertEqual(result['status'],'complete');self.assertEqual(events[-1]['counts']['reused'],size)
        self.assertLess(time.monotonic()-before,5)
        def fail(*args):raise ValueError('failed root')
        result=execute(units,{'stage':fail},workers=1,memory_bytes=1)
        self.assertEqual(result['units']['0'],'failed');self.assertEqual(result['units'][str(size-1)],'blocked')

    def test_formula_catalog_is_complete_and_source_arithmetic_drift_blocks_publication(self):
        from unittest.mock import patch
        self.assertEqual(len(fundamentals.definitions()),19)
        self.assertEqual({m.name for m in facts.catalog() if m.name.startswith('derived.')},
                         {'derived.'+f.name for f in fundamentals.definitions()})
        self.assertEqual(fundamentals.app_contract()['source_arithmetic_hash'],fundamentals.APP_ARITHMETIC_BASELINE)
        with patch.object(fundamentals,'APP_ARITHMETIC_BASELINE','0'*64):
            with self.assertRaisesRegex(ValueError,'arithmetic changed'):fundamentals.contract_hash()

    def test_historical_universe_preserves_renames_and_delisting_and_reports_unresolved_dates(self):
        base=dict(listing_id='listing:1',security_id='security:1',issuer_id='issuer:1',conids=['91'],
            exchange_code='XNYS',currency_code='USD',listing_status='inactive',list_date='2024-01-01',
            delisted_date='2026-05-01',product_type='STK',security_status='inactive',exchange_country='US',
            symbol_interval_id='interval:1',ticker='OLD',valid_from_date='2024-01-01',
            valid_to_date_exclusive='2026-03-01',interval_mapping='mapped',history_status='completed',
            history_mapping='mapped',interval_security_id='security:1',history_security_id='security:1',
            history_listing_id='listing:1',durable_issuer=1,duplicate_issuer=0,mapping_issue=0,
            source_hashes_valid=1,evidence_hash='a'*64)
        new=dict(base,symbol_interval_id='interval:2',ticker='NEW',valid_from_date='2026-03-01',
                 valid_to_date_exclusive=None,evidence_hash='b'*64)
        result=universe.resolve([base,new],date(2026,1,1),date(2026,6,30))
        self.assertTrue(result['ready']);old,renamed=result['intervals']
        self.assertEqual(old.valid_to,renamed.valid_from)
        self.assertEqual(renamed.valid_to.date(),date(2026,5,1))
        self.assertEqual(old.conid,renamed.conid);self.assertFalse(old.reason)
        for change,reason in [(dict(list_date=None),'missing_listing_start'),
                (dict(delisted_date=None),'missing_listing_end'),
                (dict(conids=['91','92']),'conflicting_or_invalid_conid_anchor'),
                (dict(history_security_id='other'),'historical_identity_mismatch')]:
            rejected=universe.resolve([dict(base,**change)],date(2026,1,1),date(2026,6,30))
            self.assertFalse(rejected['ready']);self.assertEqual(rejected['rejection_counts'],{reason:1})
        with self.assertRaisesRegex(ValueError,'Overlapping'):
            universe.resolve([base,base],date(2026,1,1),date(2026,6,30))

    def test_short_publication_preflight_exposes_missing_clocks_without_claiming_certification(self):
        queries=[]
        def query(sql):
            queries.append(sql)
            if 'short_volume_v1' in sql:return [dict(rows=10,unknown_public_time=10,exact_times=0)]
            return [dict(rows=4,unknown_public_time=1,exact_times=2,conservative_dates=1)]
        result=short_sql.clock_inventory(query,date(2026,1,1),date(2026,10,2))
        self.assertTrue(result['inspection_only'])
        self.assertTrue(all(r['repair_required'] for r in result['families'].values()))
        self.assertEqual(len(queries),2)
        self.assertTrue(all('PREWHERE' in sql and 'inserted_at' not in sql for sql in queries))

    def test_context_uses_selected_windows_and_compatible_state_without_guessed_warmup(self):
        start=date(2026,8,21)
        def calendar(first,last):
            from datetime import timedelta
            return tuple(first+timedelta(days=i) for i in range((last-first).days+1)
                         if (first+timedelta(days=i)).weekday()<5)
        config=c.CampaignConfig(start,start,short_windows_sessions=(5,20))
        result=context.plan(config,calendar=calendar)
        self.assertEqual(len(result['market_context']),1)
        self.assertEqual(len(result['short_context']),20)
        self.assertEqual(len(result['blocked']),2)
        seed=context.StateSeed(date(2026,8,18),'a'*64,True)
        seeded=context.plan(config,indicator_seed=seed,v7_seed=seed,calendar=calendar)
        self.assertFalse(seeded['blocked'])
        self.assertEqual(seeded['market_context'],(date(2026,8,19),date(2026,8,20)))
        with self.assertRaisesRegex(ValueError,'source bounds'):
            context.plan(config,rebuild_origin=date(2018,1,2),calendar=calendar)
    def test_v7_new_generation_and_session_backfill_are_identical_without_lookahead(self):
        from tests.test_streaming_level_book import engine
        a=engine();b=engine()
        cuts=boundaries(date(2026,8,21))
        times=[a.start+301,cuts['PM'].timestamp(),cuts['PM'].timestamp()+1,
               cuts['REGULAR'].timestamp(),cuts['REGULAR'].timestamp()+1,a.end]
        rows=[dict(t=t,open=10.,high=10.1,low=9.9,close=10.,volume=100.) for t in times]
        binding=CheckpointBinding('a'*64,'b'*64,'c'*64,'TEST',date(2026,8,21))
        original,state=v7.generate(a,rows,binding)
        backfilled,state2=v7.generate(b,rows,binding,restore_each=True)
        self.assertEqual([r['state_hash'] for r in original],[r['state_hash'] for r in backfilled])
        self.assertEqual(state.checkpoint(),state2.checkpoint())
        prefix,_=v7.generate(engine(),rows[:2],binding)
        self.assertEqual(prefix[0]['state_hash'],original[0]['state_hash'])
        next_state=v7.next_day_engine(state,date(2026,8,24),previous_checkpoint=original[-1])
        self.assertEqual(next_state.bars_processed,0)
        with self.assertRaisesRegex(ValueError,'AH boundary'):
            v7.next_day_engine(state,date(2026,8,24),previous_checkpoint=original[0])
    def test_acquisition_commands_delegate_without_downstream_or_destructive_flags(self):
        paths=acquisition.IngestionPaths(Path('fixture.env'))
        start=date(2026,1,2);end=date(2026,1,5)
        cmd=acquisition.updater_command(paths,start,end)
        self.assertIn('--skip-bars',cmd);self.assertIn('sip_raw_ssd',cmd)
        self.assertNotIn('--force-day-delete',cmd)
        with self.assertRaisesRegex(ValueError,'cannot hydrate'):acquisition.reporting_command(paths,start,end,hydrate=True)
        reporting=acquisition.reporting_command(paths,start,end)
        self.assertNotIn('--hydrate-missing-trades',reporting);self.assertNotIn('--flatfiles-root-win',reporting)
        self.assertEqual(acquisition.contiguous_ranges([end,start,start]),((start,start),(end,end)))
        self.assertTrue(str(paths.cache).startswith('\\\\DESKTOP-SAAI85T\\Workstation-D'))
        from unittest.mock import patch
        from pipelines.market_sip.flatfiles import download_update_events as updater
        from scripts import backfill_trade_reporting_flags as migration
        with patch.object(sys,'argv',list(cmd[2:])):
            parsed=updater.parse_args()
        self.assertTrue(parsed.skip_bars);self.assertEqual(parsed.env_file,[paths.env_file])
        self.assertFalse(migration.parse_args(list(reporting[3:])).hydrate_missing_trades)
        with patch.object(updater,'load_env_files',return_value=['fixture.env']) as loader,patch.object(Path,'is_file',return_value=True):
            self.assertEqual(updater.load_updater_environment(['--env-file','fixture.env']),['fixture.env'])
            loader.assert_called_once_with([Path('fixture.env')],verbose=True)

    def test_source_gate_requires_matching_receipts_and_idle_mutations(self):
        days=(date(2026,1,2),date(2026,1,5))
        rows=[dict(source_date=str(day),trade_event_rows=2,total_event_rows_after_filters=5,updated_at='clock') for day in days]
        def query(sql):
            return [dict(day=r['source_date'],statuses=['ok']) for r in rows] if 'events_build_manifest' in sql else rows
        inv=source.inventory(query,days[0],days[-1],sessions=days)
        receipt=[dict(source_date=str(day),revision=c.REPORTING_REVISION,source_digest=inv['source_hashes'][str(day)],
                      status='complete',details='{"counts":{"n":2,"bad":0}}') for day in days]
        active=[]
        def reporting_query(sql):return active if 'system.mutations' in sql else receipt
        reporting=source.reporting_inventory(reporting_query,inv)
        self.assertFalse(reporting['missing']);self.assertFalse(reporting['mutating'])
        def all_query(sql):return query(sql) if 'market_sip_compact.events_' in sql else reporting_query(sql)
        self.assertEqual(source.require_ready(all_query,inv,reporting),c.digest(dict(source=inv['content_hash'],reporting=reporting['content_hash'])))
        active.append(dict(mutation_id='active'))
        with self.assertRaisesRegex(ValueError,'mutating'):source.require_ready(query,inv,source.reporting_inventory(reporting_query,inv))
        receipt[0]['source_digest']='changed'
        with self.assertRaisesRegex(ValueError,'mismatch'):source.reporting_inventory(reporting_query,inv)
        rows.pop()
        self.assertEqual(source.inventory(query,days[0],days[-1],sessions=days)['missing'],(days[-1],))
    def test_complete_app_catalog_and_fact_availability_contract(self):
        from datetime import timezone
        metrics=facts.catalog()
        self.assertGreater(len(metrics),60)
        tags={tag for m in metrics for tag in m.tags}
        self.assertIn('EntityPublicFloat',tags);self.assertIn('RevenueFromContractsWithCustomers',tags)
        self.assertEqual(facts.catalog_hash(),facts.catalog_hash())
        at=facts.conservative_date_availability(date(2026,1,2))
        self.assertEqual(at,datetime(2026,1,3,tzinfo=timezone.utc))
        row=dict(dataset_id=str(uuid.uuid4()),attempt_id=str(uuid.uuid4()),entity_kind=1,entity_key=1,metric_key=2,revision=0,
                 available_at=at,evidence_hash='a'*64,confidence=100,status=1,precision=2,unit_key=1,basis_unit_key=1,
                 taxonomy_key=1,context_kind=0,fiscal_year=2026,fiscal_period='FY',value=123.)
        facts.verify_fact(row)
        for fields in (dict(available_at=at.replace(tzinfo=None)),dict(value=float('nan')),dict(status=2),dict(evidence_hash='missing'),
                       dict(basis_unit_key=0),dict(taxonomy_key=4),dict(confidence=True),dict(metric_key=2**32)):
            with self.assertRaises(ValueError):facts.verify_fact(dict(row,**fields))
    def test_dated_membership_removes_delisted_and_otc_without_future_births(self):
        from datetime import timezone,timedelta
        start=datetime(2026,1,2,9,tzinfo=timezone.utc);end=start+timedelta(days=10)
        active=ListingInterval('listing:1','security:1','issuer:1','OLD',123,start,end,'US','NASDAQ','USD','STK',True,True,False,False,'a'*64)
        renamed=replace(active,ticker='NEW',valid_from=end,valid_to=end+timedelta(days=10))
        delisted=replace(renamed,listed=False,valid_from=renamed.valid_to,valid_to=end+timedelta(days=20))
        self.assertFalse(active.reason);self.assertEqual(delisted.reason,'inactive_listing')
        self.assertEqual(replace(active,exchange_code='OTCQX').reason,'unsupported_otc_venue')
        self.assertEqual(active.row(str(uuid.uuid4()))['listing_key'],renamed.row(str(uuid.uuid4()))['listing_key'])
        validate_intervals((active,renamed,delisted))
        with self.assertRaisesRegex(ValueError,'Overlapping'):validate_intervals((active,replace(renamed,valid_from=end-timedelta(seconds=1))))
        self.assertEqual(identity_key(LISTING,'listing:1'),identity_key(LISTING,'listing:1'))
    def test_expired_keeper_session_cannot_publish_or_release_new_owner(self):
        class Lock:
            is_acquired=False
            def acquire(self,blocking):self.is_acquired=True;return True
            def contenders(self):return ['owner']
            def release(self):self.is_acquired=False
        class Client:
            connected=True;client_id=(1,b'session')
            def Lock(self,path,identifier):self.lock=Lock();return self.lock
            def add_listener(self,callback):self.listener=callback
            def remove_listener(self,callback):pass
        client=Client();claim=KeeperClaim(client,str(uuid.uuid4()),owner='owner')
        claim.check();client.client_id=(2,b'replaced')
        with self.assertRaisesRegex(RuntimeError,'expired'):claim.check()
        claim.close();self.assertTrue(client.lock.is_acquired)
        client=Client();claim=KeeperClaim(client,str(uuid.uuid4()),owner='owner')
        client.listener('SUSPENDED');client.listener('CONNECTED')
        with self.assertRaisesRegex(RuntimeError,'expired'):claim.check()
        claim.close()

    def test_transport_bounds_reject_nonfinite_and_oversized_before_write(self):
        writes=[]
        def query(*args,**kw):writes.append(args)
        with self.assertRaises(ValueError):insert_rows(query,'arte','research_facts_v1',[dict(value=float('nan'))])
        with self.assertRaises(ValueError):insert_rows(query,'arte','research_facts_v1',[dict(value='x'*100)],maximum_bytes=10)
        self.assertFalse(writes)
    def test_parallel_fanout_reuses_one_parent_and_respects_global_memory(self):
        units=(Unit('bars','bars',memory_bytes=1),Unit('indicators','consume',('bars',),1),
               Unit('v7','consume',('bars',),1))
        starts=[];peak=[0];active=[0];lock=Lock();bar_result={'hash':'shared'}
        def build(u,parents,stop):starts.append(u.key);return bar_result
        def consume(u,parents,stop):
            self.assertIs(parents['bars'],bar_result)
            with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
            time.sleep(.02)
            with lock:active[0]-=1
            return {'unit':u.key}
        result=execute(units,{'bars':build,'consume':consume},workers=2,memory_bytes=2)
        self.assertEqual(result['status'],'complete');self.assertEqual(starts,['bars']);self.assertEqual(peak[0],2)
        peak[0]=0
        execute(units,{'bars':build,'consume':consume},workers=2,memory_bytes=1)
        self.assertEqual(peak[0],1)

    def test_failed_parent_blocks_consumers_without_hiding_other_lane(self):
        def fail(*args):raise ValueError('source failure')
        def good(u,*args):return {'unit':u.key}
        result=execute((Unit('source','bad'),Unit('child','good',('source',)),Unit('reference','good')),
                       {'bad':fail,'good':good})
        self.assertEqual(result['units'],dict(source='failed',child='blocked',reference='certified'))
        with self.assertRaisesRegex(ValueError,'cycle'):
            execute((Unit('a','good',('b',)),Unit('b','good',('a',))),{'good':good})
        stop=Event();stop.set()
        self.assertEqual(execute((Unit('a','good'),),{'good':good},stop=stop)['status'],'interrupted')

    def test_all_encoded_states_reject_unknown_delayed_and_invalid(self):
        for meta in range(256):
            expected=bool(meta & 1) and meta & 192 == 64
            self.assertEqual(c.timely_trade(meta),expected)
        for invalid in (-1,256,True,1.0):
            with self.assertRaises(ValueError):c.timely_trade(invalid)
        self.assertIn('bitAnd(event_meta,192)=64',c.timely_sql())

    def test_budget_and_ranges_are_frozen(self):
        config=c.CampaignConfig(date(2026,1,2),date(2026,1,5))
        self.assertEqual(config.content_hash,replace(config).content_hash)
        for fields in (dict(workers=32),dict(start=date(2025,12,31)),dict(database='default'),
                       dict(volume_windows_seconds=(60,10)),dict(resolutions_ms=(123,))):
            with self.assertRaises(ValueError):replace(config,**fields)

    def test_every_column_has_a_lossless_codec_and_normalized_keys(self):
        self.assertEqual(len(s.TABLES),len({t.name for t in s.TABLES}))
        for table,ddl in zip(s.TABLES,s.ddl()):
            self.assertEqual(ddl.count('CODEC('),len(table.columns))
            self.assertIn("storage_policy='live_market_ssd'",ddl)
            self.assertNotIn('Float32',ddl)
        bars=next(t for t in s.TABLES if t.name=='research_bars_v1')
        self.assertIn('listing_key',[x.name for x in bars.columns])
        self.assertNotIn('ticker',[x.name for x in bars.columns])

    def test_actual_misplaced_parts_fail_even_with_correct_table_policy(self):
        def query(sql):
            if 'system.storage_policies' in sql:return [dict(disk_name='live_market_ssd')]
            if 'system.tables' in sql:return [dict(name='research_bars_v1',storage_policy='live_market_ssd')]
            return [dict(table='research_bars_v1',disk_name='default')]
        with self.assertRaisesRegex(ValueError,'parts'):s.verify_storage(query)

    def test_strict_sql_keeps_quotes_and_changes_only_trade_gate(self):
        args=('arte','test',date(2026,1,2),'TEST','00000000-0000-0000-0000-000000000001',[])
        old=market_day_sql.broker_sql(*args)
        strict=market_day_sql.broker_sql(*args,verified_timely=True)
        self.assertIn('kind=1 AND bitAnd(event_meta,192)=64',strict)
        self.assertEqual(strict.replace('bitAnd(event_meta,192)=64','bitAnd(event_meta,128)=0'),old)

    def test_session_restore_matches_uninterrupted_pending_state(self):
        from tests.test_streaming_level_book import engine,bars
        a=engine(historical=True)
        # The fixture's nonempty seed intentionally has no strict provenance.
        a.seed_input_policy=a.input_policy
        sequence=bars(a)
        for row in sequence[:8]:a.update(row)
        binding=CheckpointBinding('a'*64,'b'*64,'c'*64,'TEST',date(2026,8,21))
        boundary=datetime.fromtimestamp(sequence[7]['t'],tz=boundaries(binding.session)['PM'].tzinfo)
        payload,hash=encode(a,binding,boundary)
        b=restore(payload,hash,binding)
        for row in sequence[8:]:a.update(row);b.update(row)
        self.assertEqual(a.checkpoint(),b.checkpoint())
        with self.assertRaisesRegex(ValueError,'hash'):restore(payload+b'x',hash,binding)
        with self.assertRaisesRegex(ValueError,'binding'):restore(payload,hash,replace(binding,input_prefix_hash='d'*64))
        with self.assertRaisesRegex(ValueError,'future'):encode(a,binding,boundary)

    def test_early_close_and_missing_timezone(self):
        day=date(2026,11,27)
        normal=boundaries(day)
        early=normal['REGULAR'].replace(hour=18) # 13:00 New York in November.
        self.assertEqual(boundaries(day,early)['REGULAR'],early)
        with self.assertRaises(ValueError):boundaries(day,datetime(2026,11,27,13))


@unittest.skipUnless(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')=='1',
                     'Bounded SSD integration requires explicit test opt-in')
class ClickHouseIntegration(unittest.TestCase):
    def test_continuous_wilder_state_survives_warmup_and_ready_session_boundaries(self):
        from pipelines.certified_research import indicators
        from datetime import timedelta
        dataset=str(uuid.uuid4());prior={1000:{n:0 if n=='sample_count' else 0. for n in indicators.STATE_FIELDS}}
        offset=0;ag=al=atr=0.;previous=None;emas={};signal=None
        for day,count in ((date(2026,1,2),7),(date(2026,1,5),8),(date(2026,1,6),25)):
            unit=market.MarketUnit(dataset,day,'TEST',997,str(uuid.uuid4()))
            source=(f"SELECT toDate('{day}') session_date,toUInt32(1000) resolution_ms,toUInt32(number) bucket_index,"
                f'toUInt64(100000+((number+{offset})%5)*1000) close_int,close_int+500 high_int,close_int-500 low_int FROM numbers({count})')
            self.client.query(indicators.technical_sql(self.database,unit,source,prior,frames=(1000,)),read=False)
            actual=self.client.query(f'SELECT * FROM {self.database}.research_stage_indicators_v1 WHERE '
                +unit.where()+' ORDER BY bucket_index')
            for j,row in enumerate(actual):
                n=offset+j+1;close=10+((n-1)%5)*.1
                gain=max(close-previous,0) if previous is not None else 0
                loss=max(previous-close,0) if previous is not None else 0
                tr=max(.1,abs(close+.05-previous),abs(close-.05-previous)) if previous is not None else .1
                if n<=15:ag+=gain/14;al+=loss/14
                else:ag=ag*13/14+gain/14;al=al*13/14+loss/14
                atr=atr+tr/14 if n<=14 else atr*13/14+tr/14
                for p in market_day_sql.EMAS:
                    emas[p]=close if p not in emas else emas[p]*(1-2/(p+1))+close*2/(p+1)
                    self.assertAlmostEqual(row['ema_'+str(p)],emas[p],places=10)
                line=emas[12]-emas[26];signal=line if signal is None else signal*.8+line*.2
                self.assertAlmostEqual(row['macd_signal'],signal,places=10)
                self.assertAlmostEqual(row['avg_gain'],ag,places=10);self.assertAlmostEqual(row['avg_loss'],al,places=10)
                self.assertAlmostEqual(row['atr_14'],atr,places=10)
                self.assertEqual(int(row['sample_count']),n)
                self.assertEqual(int(row['rsi_ready']),int(n>=15));self.assertEqual(int(row['atr_ready']),int(n>=14))
                expected_rsi=0 if n<15 else 100 if al<=0 else 100-100/(1+ag/al)
                self.assertAlmostEqual(row['rsi_14'],expected_rsi,places=10);previous=close
            last=actual[-1]
            prior={1000:{name:previous if name=='close' else int(last[name]) if name=='sample_count'
                else float(last[name]) for name in indicators.STATE_FIELDS}}
            offset+=count
        broken={1000:dict(prior[1000])};broken[1000].pop('avg_gain')
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            indicators.technical_sql(self.database,unit,source,broken,frames=(1000,))

    def test_short_pressure_uses_session_windows_and_original_public_revision_clocks(self):
        from datetime import timezone
        from pipelines.certified_research import short_pressure
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4());key=9998
        metrics={m.name:m.key for m in facts.catalog()}
        parent=ReferenceScope(dataset,2,key,'short_volume',date(2026,1,1),date(2026,1,9),'a'*64,facts.catalog_hash())
        class Claim:
            def check(self):pass
        rows=[]
        for day,volume in (('2026-01-02',10),('2026-01-05',20),('2026-01-06',30)):
            for name,value in (('short.volume',volume),('short.total_volume',100)):
                rows.append(dict(dataset_id=dataset,attempt_id=attempt,entity_kind=2,entity_key=key,metric_key=metrics[name],
                    period_start=day,period_end=day,context_kind=1,fiscal_year=0,fiscal_period='',taxonomy_key=0,
                    available_at=day+' 22:00:00.000000',revision=0,value=value,unit_key=2,basis_unit_key=2,
                    status=1,precision=1,confidence=90,reason='',evidence_hash=c.digest((day,name))))
        rows.append(dict(rows[0],value=50,available_at='2026-01-08 22:00:00.000000',revision=1,evidence_hash='b'*64))
        for base,value in ((rows[0],-1),(rows[1],100)):
            rows.append(dict(base,period_start='2026-01-09',period_end='2026-01-09',value=value,
                available_at='2026-01-09 23:00:00.000000',evidence_hash=c.digest(('invalid',base['metric_key']))))
        insert_rows(self.client.query,self.database,'research_facts_v1',rows)
        publisher=ReferencePublisher(self.client.query,self.database,Claim())
        publisher.publish(parent,attempt,publisher.evidence(parent,attempt))
        def read(day,windows):
            sql=short_pressure.snapshot_sql(self.client.query,self.database,Claim(),parent,
                datetime(2026,1,day,22,tzinfo=timezone.utc),windows)
            return self.client.query(sql)
        original=read(7,(2,3,20));corrected=read(9,(2,3))
        self.assertTrue(all(r['window_end']=='2026-01-06' for r in original))
        with self.assertRaisesRegex(ValueError,'outside its certified'):
            read(10,(2,3))
        unavailable=read(1,(2,3))
        self.assertTrue(all(r['ratio'] is None and r['reason']=='no_public_short_volume' for r in unavailable))
        self.assertAlmostEqual(original[0]['ratio'],.25);self.assertAlmostEqual(original[1]['ratio'],.2)
        self.assertIsNone(original[2]['ratio']);self.assertEqual(int(original[2]['context_ready']),0)
        self.assertAlmostEqual(corrected[0]['ratio'],.25);self.assertAlmostEqual(corrected[1]['ratio'],1/3)
        self.client.query(short_pressure.staging_table().ddl(self.database),read=False)
        receipt=publisher.receipt(parent);windows=(2,3,20)
        scope=ReferenceScope(dataset,2,key,'short_pressure',parent.start,parent.end,
            short_pressure.parent_hash(receipt,windows),facts.catalog_hash())
        result=short_pressure.run(scope,parent,windows,self.client.query,Claim(),lambda:None,database=self.database)
        stored=self.client.query(f'SELECT * FROM {self.database}.research_short_pressure_v1 WHERE '
            +scope.rows_where(result['attempt_id'])+' ORDER BY available_at,window_sessions')
        before=[r for r in stored if r['available_at'].startswith('2026-01-06')]
        after=[r for r in stored if r['available_at'].startswith('2026-01-08')]
        self.assertAlmostEqual(before[1]['ratio'],.2);self.assertAlmostEqual(after[1]['ratio'],1/3)
        self.assertEqual(before[1]['window_end'],'2026-01-06')
        self.assertIsNone(after[2]['ratio']);self.assertEqual(int(after[2]['context_ready']),0)
        invalid=[r for r in stored if r['available_at'].startswith('2026-01-09')]
        self.assertTrue(all(r['ratio'] is None and int(r['context_ready'])==0 for r in invalid))
        logs=[]
        def traced(sql,*args,**kwargs):logs.append(sql);return self.client.query(sql,*args,**kwargs)
        self.assertEqual(short_pressure.run(scope,parent,windows,traced,Claim(),lambda:None,database=self.database),result)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        self.client.query(f"ALTER TABLE {self.database}.research_short_pressure_v1 UPDATE ratio=0.9 WHERE "
            +scope.rows_where(result['attempt_id'])+" AND ratio IS NOT NULL SETTINGS mutations_sync=2",read=False)
        with self.assertRaisesRegex(ValueError,'actual output'):
            short_pressure.run(scope,parent,windows,traced,Claim(),lambda:None,database=self.database)

    def test_native_ns_reporting_certificate_recomputes_flags_and_reuses_verified_evidence(self):
        from pipelines.market_sip.events import reporting_evidence
        table='native_ns_'+uuid.uuid4().hex;target='native_events_'+uuid.uuid4().hex;day=date(2026,1,2)
        self.client.query(reporting_evidence.ddl(self.database,table),read=False)
        self.client.query(f'CREATE TABLE {self.database}.{target} (ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64) '
            "ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        first=int(self.client.query("SELECT toUInt64(toUnixTimestamp64Micro(toDateTime64('2026-01-02 10:00:00',6,'America/New_York'))) t")[0]['t'])
        self.client.query(f"INSERT INTO {self.database}.{target} VALUES ('TEST',1,65,{first}),('TEST',2,1,{first+1})",read=False)
        insert_rows(self.client.query,self.database,table,[dict(source_date=str(day),ticker='TEST',ordinal=n,
            sip_timestamp_ns=(first+n-1)*1000+123,participant_timestamp_ns=first*1000-1000 if n==1 else None,
            reporting_condition_entries=[],reporting_reason=0 if n==1 else 8,source_digest='a'*64) for n in (1,2)])
        logs=[]
        def query(sql,*args,**kwargs):logs.append(sql);return self.client.query(sql,*args,**kwargs)
        counts=reporting_evidence.certify(query,self.database,self.database+'.'+target,day,'a'*64,first,first+1,2,table=table)
        self.assertEqual((counts['timely'],counts['unknown'],counts['bad']),(1,1,0))
        logs.clear()
        self.assertEqual(reporting_evidence.certify(query,self.database,self.database+'.'+target,day,'a'*64,first,first+1,2,table=table),counts)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        self.client.query(f"ALTER TABLE {self.database}.{target} UPDATE event_meta=65 WHERE ordinal=2 SETTINGS mutations_sync=2",read=False)
        with self.assertRaisesRegex(ValueError,'does not match complete'):
            reporting_evidence.certify(query,self.database,self.database+'.'+target,day,'a'*64,first,first+1,2,table=table)

    def test_original_reporting_ns_projection_reconciles_canonical_fields_and_preserves_unknowns(self):
        from pipelines.market_sip.events import reporting_evidence
        table='ns_canonical_'+uuid.uuid4().hex
        self.client.query(f'CREATE TABLE {self.database}.{table} (ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64) '
            "ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        self.client.query(f"INSERT INTO {self.database}.{table} VALUES ('TEST',1,65,1000000),('TEST',2,1,1000001)",read=False)
        raw=("SELECT 'TEST' ticker,number+1 sequence_number,toUInt8(if(number=0,65,1)) event_meta,"
            "toUInt64(1000000+number) sip_timestamp_us,toUInt64(1000000000+number*1000+123) sip_timestamp_ns,"
            "if(number=0,toNullable(toUInt64(999999123)),CAST(NULL AS Nullable(UInt64))) participant_timestamp_ns,"
            "['5','13','30','31','32','33'] reporting_condition_entries,toUInt8(if(number=0,0,8)) reporting_reason FROM numbers(2)")
        sql=reporting_evidence.reconciled_projection(raw,self.database+'.'+table,'2026-01-02','a'*64,
            ['sip_timestamp_us'],first_sip_us=1000000,last_sip_us=1000001)
        rows=self.client.query(sql)
        self.assertEqual(len(rows),2);self.assertFalse(any(int(r['mismatch']) or int(r['ambiguous']) for r in rows))
        self.assertEqual(int(rows[0]['sip_timestamp_ns']),1000000123)
        self.assertIsNone(rows[1]['participant_timestamp_ns'])
        self.assertEqual(len(rows[0]['reporting_condition_entries']),6)
        self.client.query(reporting_evidence.ddl(self.database),read=False)
        self.client.query(f'INSERT INTO {self.database}.{reporting_evidence.TABLE} SELECT * EXCEPT(mismatch,ambiguous) FROM ({sql})',read=False)
        stored=self.client.query(f'SELECT participant_timestamp_ns FROM {self.database}.{reporting_evidence.TABLE} ORDER BY ordinal')
        self.assertEqual(stored[1]['participant_timestamp_ns'],None)

    def test_live_clock_evidence_preflight_reports_precision_limit_without_reading_events(self):
        queries=[]
        def query(sql):queries.append(sql);return self.client.query(sql+' SETTINGS readonly=1')
        result=source.clock_evidence_inventory(query,date(2026,1,2),date(2026,1,5))
        self.assertFalse(result['reporting_ready'])
        self.assertTrue(any(r['name']=='execution_timestamp_us' for r in result['columns']))
        self.assertFalse(any('FROM market_sip_compact.events_' in sql for sql in queries))

    def test_configurable_activity_windows_use_elapsed_time_and_certified_context(self):
        from pipelines.certified_research import activity
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4());day=date(2026,8,21)
        class Claim:
            def check(self):pass
        parent=Scope(dataset,day,991,'broker_100ms','a'*64)
        rows=[dict(dataset_id=dataset,attempt_id=attempt,session_date=str(day),listing_key=991,
            resolution_ms=100,bucket_index=bucket,volume=volume,notional=volume*10,trade_count=1,
            execution_volume=volume,execution_notional=volume*10) for bucket,volume in
            ((4*3600*10+5,7.),(9*3600*10,2.),(9*3600*10+50,3.),(9*3600*10+200,5.))]
        insert_rows(self.client.query,self.database,'research_liquidity_v1',rows)
        publisher=Publisher(self.client.query,self.database,Claim())
        receipt=publisher.publish(parent,attempt,expected=publisher.evidence(parent,attempt))
        windows=(10,60,120)
        output=Scope(dataset,day,991,'activity',activity.parent_hash(windows,[(parent,receipt)]))
        plans=(PlannedUnit(Unit('shared','market_aggregate'),dict(day=str(day),listing_key=991,ticker='TEST')),
            PlannedUnit(Unit('activity','activity',('shared',)),dict(day=str(day),listing_key=991,windows=windows)))
        handlers=ActivityHandlers(dataset,self.database,Claim(),lambda:self.builder.Client(self.args,persistent=False),lambda:None,plans)
        inputs={'shared':dict(receipts={'broker_100ms':receipt})}
        handlers.producer(plans[-1],inputs,Event());result=handlers.verifier(plans[-1],inputs)
        facts_rows=self.client.query(f"SELECT window_seconds,volume,trade_count,context_ready FROM {self.database}.research_activity_v1 "
            f"WHERE attempt_id=toUUID('{result['attempt_id']}') AND bucket_index={9*3600*10+200} ORDER BY window_seconds")
        self.assertEqual([r['volume'] for r in facts_rows],[5.,10.,10.])
        self.assertEqual([int(r['trade_count']) for r in facts_rows],[1,3,3])
        self.assertTrue(all(int(r['context_ready'])==1 for r in facts_rows))
        early=self.client.query(f"SELECT volume,context_ready FROM {self.database}.research_activity_v1 "
            f"WHERE attempt_id=toUUID('{result['attempt_id']}') AND bucket_index={4*3600*10+5}")
        self.assertTrue(all(r['volume'] is None and int(r['context_ready'])==0 for r in early))
        logs=[]
        def query(sql,*args,**kwargs):logs.append(sql);return self.client.query(sql,*args,**kwargs)
        self.assertEqual(activity.run(output,windows,[parent],query,Claim(),lambda:None,database=self.database),result)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))

    def test_live_capacity_metadata_covers_source_bytes_and_designated_disks(self):
        from pipelines.certified_research import capacity
        result=capacity.inventory(lambda sql:self.client.query(sql+' SETTINGS readonly=1'))
        self.assertEqual({r['name'] for r in result['disks']},{'live_market_ssd','sip_raw_ssd'})
        self.assertGreater(sum(r['compressed_bytes'] for r in result['tables'] if r['database']=='market_sip_compact'),0)
        self.assertTrue(all(r['compressed_bytes']<=r['disk_bytes'] for r in result['tables']))
        self.assertFalse(result['projection_ready'])

    def test_imported_source_statistics_are_read_from_canonical_certificate(self):
        from unittest.mock import patch
        from pipelines.market_sip.flatfiles import download_update_events as updater
        with patch.object(sys,'argv',['updater','--start-date','2026-01-02','--end-date','2026-01-02','--skip-bars']):
            args=updater.parse_args()
        builder=self.client
        class Client:
            def query_tsv(self,sql):
                rows=builder.query(sql.replace(' FORMAT TSV',' SETTINGS readonly=1'))
                return '\n'.join('\t'.join(str(v) for v in row.values()) for row in rows)
        job=updater.DownloadJob('trades','2026-01-02','unavailable.csv.gz','unavailable.csv.gz',0)
        stats=updater.certified_source_day_stats(Client(),args,updater.DayFiles('2026-01-02',job,job))
        self.assertGreater(stats.rows,0);self.assertEqual(stats.quote_rows+stats.trade_rows,stats.rows)
        self.assertLessEqual(stats.first_sip_timestamp_us,stats.last_sip_timestamp_us)

    def test_universe_publication_resumes_partial_membership_and_rechecks_authority(self):
        from datetime import timezone,timedelta
        from hashlib import sha256
        dataset=str(uuid.uuid4());start=datetime(2026,1,2,tzinfo=timezone.utc)
        proof='["gateway","'+dataset+'"]';proof_hash=sha256(proof.encode()).hexdigest()
        active=ListingInterval('listing:'+dataset,'security:'+dataset,'issuer:'+dataset,'TEST',91,
            start,start+timedelta(days=1),'US','NASDAQ','USD','STK',True,True,False,False,proof_hash)
        dead=replace(active,listed=False,valid_from=active.valid_to,valid_to=active.valid_to+timedelta(days=1))
        snapshot=dict(ready=True,rejected=(),intervals=(active,dead),source_hash='a'*64)
        class Claim:
            def check(self):pass
        dictionary=Claim();dictionary.dictionary_database=self.database;logs=[]
        def query(sql,*args,**kwargs):logs.append(sql);return self.client.query(sql,*args,**kwargs)
        publisher=UniversePublisher(query,self.database,dataset,Claim(),dictionary,lambda:None)
        fixture=(f'SELECT {c.literal(active.listing_id)} listing_id,{c.literal(proof)} source_proof,'
                 f'{c.literal(proof_hash)} evidence_hash')
        insert_rows(query,self.database,'research_membership_v1',[active.row(dataset)])
        self.assertIsNone(publisher.receipt(snapshot))
        receipt=publisher.publish(snapshot,date(2026,1,2),date(2026,1,3),date(2026,10,12),fixture_projection=fixture)
        self.assertEqual(receipt['intervals'],2)
        self.assertEqual(len(membership_at(query,self.database,dataset,start)),1)
        self.assertFalse(membership_at(query,self.database,dataset,active.valid_to))
        logs.clear()
        self.assertEqual(publisher.publish(snapshot,date(2026,1,2),date(2026,1,3),date(2026,10,12),fixture_projection=fixture),receipt)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        with self.assertRaisesRegex(ValueError,'Unresolved historical'):
            publisher.publish(dict(snapshot,ready=False,rejected=({'reason':'missing_listing_start'},)),
                date(2026,1,2),date(2026,1,3),date(2026,10,12),fixture_projection=fixture)
        insert_rows(query,self.database,'research_membership_v1',[dead.row(dataset)])
        with self.assertRaisesRegex(ValueError,'Membership changed, duplicated'):publisher.receipt(snapshot)

    def test_concrete_sec_and_derived_handlers_run_through_campaign_controller(self):
        dataset=str(uuid.uuid4());start=date(2026,1,1);end=date(2026,10,2)
        config=c.CampaignConfig(start,end,workers=2)
        common=dict(entity_key=9918,start='2025-01-01',end=str(end))
        plans=(PlannedUnit(Unit('sec','fundamentals',(),config.query_memory_bytes),dict(common,cik='320193',source_hash='a'*64)),
            PlannedUnit(Unit('derived','derived_fundamentals',('sec',),config.query_memory_bytes),common))
        class Claim:
            def check(self):pass
        dictionary_events=[];database=self.database
        class Dictionary:
            dictionary_database=database
            def check(self):dictionary_events.append('check')
            def close(self):dictionary_events.append('close')
        def dictionary():dictionary_events.append('acquire');return Dictionary()
        claim=Claim();registry=ReferenceHandlers(dataset,self.database,claim,
            lambda:self.builder.Client(self.args,persistent=False),lambda:None,plans,dictionary_claim_factory=dictionary)
        producers,verifiers=registry.registry()
        campaign=Campaign(self.client.query,self.database,claim,dataset,config,plans,'a'*64,lambda:None,producers,verifiers)
        result=campaign.run();self.assertEqual(result['status'],'complete',str(result['errors']))
        self.assertEqual(result['seal']['units'],2)
        self.assertEqual(dictionary_events.count('acquire'),2);self.assertEqual(dictionary_events.count('close'),2)
        dictionary_events.clear()
        self.assertEqual(campaign.run()['seal'],result['seal'])
        self.assertFalse(dictionary_events)

    def test_campaign_publishes_only_verified_authorities_and_detects_tampering_on_resume(self):
        dataset=str(uuid.uuid4());day=date(2026,1,2)
        config=c.CampaignConfig(day,day,workers=2,query_memory_bytes=64*1024**2,global_memory_bytes=128*1024**2)
        plans=(PlannedUnit(Unit('source','facts',(),64*1024**2),dict(value=1.,family='fundamentals')),
               PlannedUnit(Unit('child','facts',('source',),64*1024**2),dict(value=2.,family='derived_fundamentals')))
        class Claim:
            def check(self):pass
        claim=Claim();publisher=ReferencePublisher(self.client.query,self.database,claim);logs=[]
        def query(sql,*args,**kw):logs.append(sql);return self.client.query(sql,*args,**kw)
        def scope(plan,parents):
            parent=c.digest({k:v['output_hash'] for k,v in parents.items()}) if parents else 'a'*64
            return ReferenceScope(dataset,1,9919,plan.arguments['family'],day,day,parent,facts.catalog_hash())
        def verifier(plan,parents):return publisher.receipt(scope(plan,parents))
        def produce(plan,parents,stop):
            attempt=str(uuid.uuid4())
            row=dict(dataset_id=dataset,attempt_id=attempt,entity_kind=1,entity_key=9919,metric_key=1 if plan.unit.key=='source' else 2,
                period_start=None,period_end=str(day),context_kind=1,fiscal_year=2026,fiscal_period='FY',taxonomy_key=1,
                available_at='2026-01-02 12:00:00.000000',revision=0,value=plan.arguments['value'],unit_key=1,basis_unit_key=1,
                status=1,precision=1,confidence=100,reason='',evidence_hash='a'*64)
            insert_rows(self.client.query,self.database,'research_facts_v1',[row])
            bound=scope(plan,parents);publisher.publish(bound,attempt,publisher.evidence(bound,attempt))
        campaign=Campaign(query,self.database,claim,dataset,config,plans,'a'*64,lambda:None,{'facts':produce},{'facts':verifier})
        result=campaign.run();self.assertEqual(result['status'],'complete');self.assertEqual(result['seal']['units'],2)
        inspection=campaign_status(query,self.database,dataset)
        self.assertTrue(inspection['inspection_only']);self.assertEqual(inspection['counts']['certified'],2)
        self.assertEqual(len(campaign.verify()),64)
        logs.clear();self.assertEqual(campaign.run()['seal'],result['seal'])
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        self.client.query(f"ALTER TABLE {self.database}.research_facts_v1 UPDATE value=9 WHERE dataset_id=toUUID('{dataset}') AND metric_key=2 SETTINGS mutations_sync=2",read=False)
        with self.assertRaisesRegex(ValueError,'match'):campaign.run()

    def test_regular_price_facts_use_early_close_and_exclude_pm_and_ah(self):
        from datetime import timezone
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4());day=date(2026,11,27);listing=9910;security=9911
        class Claim:
            def check(self):pass
        base=dict(dataset_id=dataset,session_date=str(day),listing_key=listing,attempt_id=attempt,resolution_ms=1000,
            price_valid=1,extremes_valid=1)
        bars=[dict(base,bucket_index=bucket,open_int=price,high_int=price,low_int=price,close_int=price)
            for bucket,price in ((9*3600,100000),(9*3600+35*60,200000),(13*3600-1,300000),(13*3600,990000))]
        insert_rows(self.client.query,self.database,'research_bars_v1',bars)
        bar_scope=Scope(dataset,day,listing,'bars','a'*64)
        publisher=Publisher(self.client.query,self.database,Claim())
        receipt=publisher.publish(bar_scope,attempt,expected=publisher.evidence(bar_scope,attempt))
        insert_rows(self.client.query,self.database,'research_membership_v1',[
            dict(dataset_id=dataset,listing_key=listing,security_key=security,issuer_key=9912,ticker='TEST',conid=91,
                 valid_from='2026-11-27 00:00:00.000000',valid_to='2026-11-28 00:00:00.000000',
                 eligible=1,reason='',evidence_hash='a'*64)])
        scope=ReferenceScope(dataset,2,security,'regular_prices',day,day,price_inputs.parent_hash([(bar_scope,receipt)]),facts.catalog_hash())
        reference_producer.run_regular_prices(scope,[bar_scope],self.client.query,Claim(),lambda:None,database=self.database)
        before=self.client.query(facts.lookup_sql(self.database,dataset,[(2,security)],[float_data.key('market.regular_close')],
            datetime(2026,11,27,17,59,tzinfo=timezone.utc)))
        self.assertFalse(before)
        rows=self.client.query(facts.lookup_sql(self.database,dataset,[(2,security)],[float_data.key('market.regular_close')],
            datetime(2026,11,27,18,tzinfo=timezone.utc)))
        self.assertEqual(rows[0]['value'],30.)

    def test_float_supply_uses_certified_allocation_prices_and_executed_split_vintages(self):
        from datetime import timezone,timedelta
        dataset=str(uuid.uuid4());security=9921;issuer=9922
        class Claim:
            def check(self):pass
        publisher=ReferencePublisher(self.client.query,self.database,Claim());parents={};parent_scopes={}
        def source_row(kind,key,metric,value,measured,available,unit=2,revision=0,period_start=None,period_end=None):
            return dict(dataset_id=dataset,entity_kind=kind,entity_key=key,metric_key=metric,value=value,
                period_start=period_start,period_end=period_end or measured,context_kind=1,fiscal_year=0,fiscal_period='',
                taxonomy_key=3 if kind==1 else 0,available_at=available,revision=revision,unit_key=unit,basis_unit_key=2 if unit==5 else unit,
                status=1,precision=1,confidence=90,reason='',evidence_hash=c.digest((kind,key,metric,value,available,revision)))
        sources={
            'fundamentals':[
                source_row(1,issuer,fundamentals.metric('SEC public float value'),500.,'2024-12-31','2025-01-02 12:00:00.000000',1),
                source_row(1,issuer,fundamentals.metric('Common shares outstanding'),100.,'2024-12-31','2025-01-02 12:00:00.000000')],
            'regular_prices':[source_row(2,security,float_data.key('market.regular_close'),10.,'2024-12-31','2024-12-31 21:00:00.000000',5)],
            'float_allocation':[source_row(2,security,float_data.key('identity.float_allocation'),1.,'2026-06-30',
                '2024-12-01 00:00:00.000000',3,period_start='2024-12-01')],
            'corporate_actions':[
                source_row(2,security,float_data.key('corporate.announced_split_ratio'),2.,'2025-01-10','2025-01-05 12:00:00.000000',3),
                source_row(2,security,float_data.key('corporate.executed_split_ratio'),2.,'2025-01-10','2025-01-10 14:00:00.000000',3),
                source_row(2,security,float_data.key('corporate.executed_split_ratio'),3.,'2025-01-10','2025-01-12 12:00:00.000000',3,1)],
            'reported_supply':[
                source_row(2,security,float_data.key('float.reported'),80.,'2025-01-15','2025-01-15 12:00:00.000000'),
                source_row(2,security,float_data.key('shares.outstanding'),250.,'2025-01-15','2025-01-15 12:00:00.000000')],
        }
        for family,rows in sources.items():
            attempt=str(uuid.uuid4());rows=[dict(row,attempt_id=attempt) for row in rows]
            insert_rows(self.client.query,self.database,'research_facts_v1',rows)
            kind,key=(1,issuer) if family=='fundamentals' else (2,security)
            parent_scope=ReferenceScope(dataset,kind,key,family,date(2024,12,1),date(2026,9,30),'a'*64,facts.catalog_hash())
            parent_scopes[family]=parent_scope
            parents[family]=publisher.publish(parent_scope,attempt,publisher.evidence(parent_scope,attempt))
        scope=ReferenceScope(dataset,2,security,'float_supply',date(2025,1,1),date(2026,9,30),float_data.parent_hash(parents),facts.catalog_hash())
        logs=[]
        plans=tuple(PlannedUnit(Unit(family,family),dict(entity_key=p.entity_key,start=str(p.start),end=str(p.end)))
                    for family,p in parent_scopes.items())
        supply=PlannedUnit(Unit('supply','float_supply',tuple(parents)),
            dict(entity_key=security,issuer_key=issuer,start=str(scope.start),end=str(scope.end)))
        def factory():
            real=self.builder.Client(self.args,persistent=False)
            class Client:
                def query(self,sql,*args,**kwargs):logs.append(sql);return real.query(sql,*args,**kwargs)
                def close(self):real.close()
            return Client()
        handlers=ReferenceHandlers(dataset,self.database,Claim(),factory,lambda:None,(*plans,supply))
        handlers.producer(supply,parents,Event());receipt=handlers.verifier(supply,parents)
        def read(name,at):
            return self.client.query(facts.lookup_sql(self.database,dataset,[(2,security)],[float_data.key(name)],at,
                units=(2,),basis_units=(2,),taxonomies=(0,)))[0]
        def at(year,month,day,hour=12):return datetime(year,month,day,hour,tzinfo=timezone.utc)
        self.assertEqual(read('float.estimated',at(2025,1,9))['value'],50.)
        self.assertEqual(read('float.estimated',at(2025,1,10))['reason'],'unconfirmed_split_execution')
        self.assertIsNone(read('float.estimated',at(2025,1,10))['value'])
        self.assertEqual(read('float.estimated',at(2025,1,11))['value'],100.)
        self.assertEqual(read('float.estimated',at(2025,1,13))['value'],150.) # Replacement correction, not 2*3.
        self.assertEqual(read('float.upper_bound',at(2025,1,13))['value'],300.)
        self.assertEqual(read('float.reported',at(2025,1,16))['value'],80.)
        self.assertEqual(read('float.lower_bound',at(2025,1,16))['value'],80.)
        self.assertEqual(read('float.upper_bound',at(2025,1,16))['value'],80.)
        self.assertEqual(read('shares.outstanding',at(2025,1,16))['value'],250.)
        expiry=date(2024,12,31)+timedelta(days=521)
        stale=read('float.estimated',at(expiry.year,expiry.month,expiry.day+1))
        self.assertIsNone(stale['value']);self.assertEqual(stale['reason'],'stale_sec_public_float')
        allocation_expired=read('float.estimated',at(2026,7,1))
        self.assertIsNone(allocation_expired['value'])
        self.assertEqual(allocation_expired['reason'],'unqualified_issuer_security_allocation')
        logs.clear()
        handlers.producer(supply,parents,Event())
        self.assertEqual(handlers.verifier(supply,parents),receipt)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))

    def test_real_sec_to_derived_chain_uses_original_fiscal_context_in_clickhouse(self):
        dataset=str(uuid.uuid4());key=993
        class Claim:
            def check(self):pass
        parent_scope=ReferenceScope(dataset,1,key,'fundamentals',date(2025,1,1),date(2026,10,2),'a'*64,facts.catalog_hash())
        before=time.monotonic()
        parent=reference_producer.run_sec(parent_scope,'320193',facts.catalog(),self.client.query,Claim(),lambda:None,database=self.database)
        scope=ReferenceScope(dataset,1,key,'derived_fundamentals',parent_scope.start,parent_scope.end,
            reference_producer.derived_parent_hash(parent),facts.catalog_hash())
        result=reference_producer.run_derived(scope,parent_scope,self.client.query,Claim(),lambda:None,database=self.database)
        self.assertGreater(int(parent['output_rows']),0);self.assertGreater(int(result['output_rows']),0)
        audit=self.client.query(f"SELECT countIf(status=1) valid,countIf(fiscal_period='FY') annual,"
            f"countIf(status=1 AND context_kind=0) unknown_context_valid FROM {self.database}.research_facts_v1 "
            f"WHERE attempt_id=toUUID('{result['attempt_id']}')")[0]
        self.assertGreater(int(audit['valid']),0);self.assertGreater(int(audit['annual']),0)
        self.assertEqual(int(audit['unknown_context_valid']),0)
        self.assertLess(time.monotonic()-before,45) # Same bounded client budget as the campaign queries.

    def test_all_nineteen_derived_formulas_are_causal_aligned_and_resume_without_inserts(self):
        from datetime import timezone
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4());key=994
        parent_scope=ReferenceScope(dataset,1,key,'fundamentals',date(2025,1,1),date(2026,3,1),'a'*64,facts.catalog_hash())
        class Claim:
            def check(self):pass
        def row(label,value,year=2024,instant=False,stamp='2025-02-10 12:00:00.000000',revision=0):
            unit=2 if 'shares' in label else 1
            return dict(dataset_id=dataset,attempt_id=attempt,entity_kind=1,entity_key=key,metric_key=fundamentals.metric(label),
                period_start=None if instant else f'{year}-01-01',period_end=f'{year}-12-31',context_kind=1 if instant else 2,
                fiscal_year=year,fiscal_period='FY',taxonomy_key=1,available_at=stamp,revision=revision,value=float(value),
                unit_key=unit,basis_unit_key=unit,status=1,precision=1,confidence=90,reason='',evidence_hash=c.digest((label,value,year,stamp)))
        current={'Revenue':100,'Gross profit':40,'Operating income':20,'Net income':10,'Operating cash flow':30,
            'Capital expenditure':-5,'Interest expense':2,'Research & development':4,'SG&A expense':8,
            'Weighted average basic shares':100,'Weighted average diluted shares':105}
        balances={'Assets':200,"Stockholders' equity":100,'Current assets':60,'Current liabilities':30,
                  'Cash':25,'Total borrowings':50,'Debt current component':20,'Debt noncurrent component':30}
        rows=[row(label,value) for label,value in current.items()]+[row(label,value,instant=True) for label,value in balances.items()]
        rows += [row('Revenue',80,2023),row('Net income',4,2023),row('Weighted average basic shares',90,2023)]
        rows += [row('Gross profit',50,stamp='2025-05-10 12:00:00.000000',revision=1)]
        rows += [dict(row('Revenue',1000,2025,stamp='2025-05-10 12:00:00.000000'),
                      period_end='2025-03-31',fiscal_period='Q1')]
        rows += [row('Cash',25,2025,True,'2026-02-10 12:00:00.000000'),
                 row('Debt current component',20,2025,True,'2026-02-10 12:00:00.000000')]
        rows += [dict(row('Gross profit',999,2025,stamp='2026-02-20 12:00:00.000000'),period_start=None,context_kind=0)]
        rows += [dict(row('Revenue',value,year),unit_key=8,basis_unit_key=8,taxonomy_key=2)
                 for value,year in ((200,2024),(150,2023))]
        insert_rows(self.client.query,self.database,'research_facts_v1',rows)
        publisher=ReferencePublisher(self.client.query,self.database,Claim())
        parent=publisher.publish(parent_scope,attempt,publisher.evidence(parent_scope,attempt))
        scope=ReferenceScope(dataset,1,key,'derived_fundamentals',parent_scope.start,parent_scope.end,
            reference_producer.derived_parent_hash(parent),facts.catalog_hash())
        logs=[]
        def query(sql,*args,**kw):logs.append(sql);return self.client.query(sql,*args,**kw)
        receipt=reference_producer.run_derived(scope,parent_scope,query,Claim(),lambda:None,database=self.database)
        def read(at):
            return {next(f.name for f in fundamentals.definitions() if f.key==int(r['metric_key'])):r
                for r in self.client.query(facts.lookup_sql(self.database,dataset,[(1,key)],
                    [f.key for f in fundamentals.definitions()],at,basis_units=(1,2),taxonomies=(1,)))}
        expected={'free_cash_flow':25,'gross_margin':40,'operating_margin':20,'net_margin':10,'free_cash_flow_margin':25,
            'return_on_assets':5,'return_on_equity':10,'working_capital':30,'current_ratio':2,'debt_to_equity':.5,
            'net_debt':25,'interest_coverage':10,'revenue_growth':25,'earnings_growth':150,'share_growth':100/9,
            'dilution':5,'cash_conversion':3,'research_intensity':4,'sga_intensity':8}
        self.assertFalse(read(datetime(2025,2,10,11,tzinfo=timezone.utc)))
        initial=read(datetime(2025,2,10,12,tzinfo=timezone.utc));self.assertEqual(set(initial),set(expected))
        for name,value in expected.items():
            self.assertAlmostEqual(initial[name]['value'],value,msg=name)
        self.assertEqual(initial['return_on_assets']['reason'],'point_in_time_approximation')
        revised=read(datetime(2025,5,11,tzinfo=timezone.utc));self.assertEqual(revised['gross_margin']['value'],50.)
        self.assertEqual(revised['revenue_growth']['value'],25.) # FY policy is not replaced by an unmatched quarter.
        late=read(datetime(2026,2,11,tzinfo=timezone.utc));self.assertIsNone(late['net_debt']['value'])
        self.assertEqual(late['net_debt']['reason'],'missing_or_invalid_aligned_inputs')
        self.assertEqual(late['gross_margin']['value'],50.) # Later unknown-context filing is still hidden.
        unknown=read(datetime(2026,2,21,tzinfo=timezone.utc))['gross_margin']
        self.assertIsNone(unknown['value']);self.assertEqual(unknown['reason'],'unknown_primary_context')
        native=self.client.query(facts.lookup_sql(self.database,dataset,[(1,key)],
            [next(f.key for f in fundamentals.definitions() if f.name=='revenue_growth')],
            datetime(2025,5,11,tzinfo=timezone.utc),units=(6,),basis_units=(8,),taxonomies=(2,)))
        self.assertEqual(len(native),1);self.assertAlmostEqual(native[0]['value'],100/3)
        absent=self.client.query(facts.lookup_sql(self.database,dataset,[(1,key)],
            [next(f.key for f in fundamentals.definitions() if f.name=='gross_margin')],
            datetime(2025,5,11,tzinfo=timezone.utc),basis_units=(8,),taxonomies=(2,)))[0]
        self.assertIsNone(absent['value']);self.assertEqual(absent['reason'],'missing_primary_input')
        self.assertEqual(int(absent['precision']),1)
        self.assertLess(int(receipt['output_rows']),19*3) # Change records, not repeated unchanged snapshots.
        logs.clear()
        self.assertEqual(reference_producer.run_derived(scope,parent_scope,query,Claim(),lambda:None,database=self.database),receipt)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))

    def test_gateway_historical_universe_read_path_is_bounded_and_preserves_diagnostics(self):
        result=universe.load(lambda sql:self.client.query(sql+' SETTINGS readonly=1'),
            date(2025,1,1),date(2026,10,2),date(2026,10,12))
        self.assertGreater(len(result['intervals']),0)
        self.assertLessEqual(len(result['source_rows']),100000)
        self.assertEqual(result['ready'],not result['rejected'])
        self.assertEqual(sum(result['rejection_counts'].values()),len(result['rejected']))
        self.assertTrue(all('source_proof' not in r and 'security_id' in r and 'issuer_id' in r for r in result['source_rows']))
        for interval in result['intervals']:
            self.assertLess(interval.valid_from,interval.valid_to)
            self.assertGreater(interval.conid,0)
            self.assertEqual(interval.exchange_country,'US')

    def test_short_producer_uses_public_clock_matching_denominator_and_verified_resume(self):
        from datetime import timezone
        dataset=str(uuid.uuid4());key=997
        class Claim:
            def check(self):pass
        for family in ('short_volume','short_interest'):
            self.client.query(f'CREATE TABLE IF NOT EXISTS {self.database}.market_{family}_v1 '
                f'AS q_live.market_{family}_v1 ENGINE=ReplacingMergeTree(inserted_at) ORDER BY {family}_id '
                "SETTINGS storage_policy='live_market_ssd',default_compression_codec='ZSTD(1)'",read=False)
        insert_rows(self.client.query,self.database,'research_membership_v1',[
            dict(dataset_id=dataset,listing_key=999,security_key=key,issuer_key=998,ticker='TEST',conid=91,
                 valid_from='2026-01-01 00:00:00.000000',valid_to='2027-01-01 00:00:00.000000',
                 eligible=1,reason='',evidence_hash='a'*64)])
        row=dict(short_volume_id='short-test',symbol_id='',listing_id='',security_id='',source_system='finra',
                 source_venue='CNMS',provider_ticker='TEST',trade_date='2026-01-05',published_at_utc='2026-01-05 23:00:00.000',
                 short_volume=20,total_volume=100,exempt_volume=2,non_exempt_volume=18,short_volume_ratio=.2,
                 source_event_key='test',source_evidence_ref='https://example.test/finra',source_run_id='',
                 source_content_sha256='a'*64,inserted_at='2026-02-01 00:00:00.000')
        zero=dict(row,short_volume_id='short-zero',trade_date='2026-01-07',published_at_utc='2026-01-07 23:00:00.000',
                  short_volume=0,total_volume=0,exempt_volume=0,non_exempt_volume=0,short_volume_ratio=None)
        insert_rows(self.client.query,self.database,'market_short_volume_v1',[row,zero])
        def scope(family):
            audit=self.client.query(short_sql.short_audit(self.database,dataset,key,date(2026,1,1),
                date(2026,1,31),family,source_database=self.database))[0]
            return ReferenceScope(dataset,2,key,family,date(2026,1,1),date(2026,1,31),audit['source_hash'],facts.catalog_hash())
        volume_scope=scope('short_volume');logs=[]
        def query(sql,*args,**kw):logs.append(sql);return self.client.query(sql,*args,**kw)
        receipt=reference_producer.run_short(volume_scope,query,Claim(),lambda:None,database=self.database,source_database=self.database)
        self.assertEqual(int(receipt['output_rows']),10)
        metric=next(m.key for m in facts.catalog() if m.name=='short.volume_ratio')
        def at(hour):return self.client.query(facts.lookup_sql(self.database,dataset,[(2,key)],[metric],datetime(2026,1,5,hour,tzinfo=timezone.utc)))
        self.assertFalse(at(22));self.assertEqual(at(23)[0]['value'],.2)
        zero_ratio=self.client.query(facts.lookup_sql(self.database,dataset,[(2,key)],[metric],datetime(2026,1,8,tzinfo=timezone.utc)))[0]
        self.assertIsNone(zero_ratio['value']);self.assertEqual(int(zero_ratio['status']),2)
        self.assertEqual(zero_ratio['reason'],'zero_source_denominator')
        volume_metric=next(m.key for m in facts.catalog() if m.name=='short.volume')
        zero_volume=self.client.query(facts.lookup_sql(self.database,dataset,[(2,key)],[volume_metric],datetime(2026,1,8,tzinfo=timezone.utc)))[0]
        self.assertEqual(zero_volume['value'],0.);self.assertEqual(int(zero_volume['status']),1)
        logs.clear()
        self.assertEqual(reference_producer.run_short(volume_scope,query,Claim(),lambda:None,
            database=self.database,source_database=self.database),receipt)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        interest=dict(short_interest_id='interest-test',symbol_id='',listing_id='',security_id='',source_system='massive',
            source_venue=None,provider_ticker='TEST',settlement_date='2026-01-05',publication_date='2026-01-15',
            published_at_utc=None,short_interest=30,avg_daily_volume=10,days_to_cover=3.,source_event_key='test',
            source_evidence_ref='https://example.test/interest',source_run_id='',source_content_sha256='b'*64,
            inserted_at='2026-02-01 00:00:00.000')
        insert_rows(self.client.query,self.database,'market_short_interest_v1',[interest])
        interest_receipt=reference_producer.run_short(scope('short_interest'),query,Claim(),lambda:None,
            database=self.database,source_database=self.database)
        self.assertEqual(int(interest_receipt['output_rows']),3)
        missing=dict(row,short_volume_id='missing-time',trade_date='2026-01-06',published_at_utc=None)
        insert_rows(self.client.query,self.database,'market_short_volume_v1',[missing])
        logs.clear()
        with self.assertRaisesRegex(ValueError,'unknown_public_time'):
            reference_producer.run_short(scope('short_volume'),query,Claim(),lambda:None,
                database=self.database,source_database=self.database)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))

    def test_real_sec_producer_normalizes_once_and_certifies_public_vintages(self):
        dataset=str(uuid.uuid4());selected=tuple(m for m in facts.catalog() if m.name=='fundamental.cash')
        scope=ReferenceScope(dataset,1,991,'fundamentals',date(2026,1,1),date(2026,8,21),'a'*64,facts.catalog_hash())
        class Claim:
            def check(self):pass
        logs=[]
        def query(sql,*args,**kwargs):logs.append(sql);return self.client.query(sql,*args,**kwargs)
        receipt=reference_producer.run_sec(scope,'320193',selected,query,Claim(),lambda:None,database=self.database)
        self.assertGreater(int(receipt['output_rows']),0)
        rows=self.client.query(facts.lookup_sql(self.database,dataset,[(1,991)],[selected[0].key],
            datetime(2026,8,21,12,tzinfo=boundaries(date(2026,8,21))['PM'].tzinfo)))
        self.assertEqual(len(rows),1);self.assertEqual(int(rows[0]['status']),1)
        proof=self.client.query(f"SELECT count() n FROM {self.database}.research_provenance_v1")
        self.assertGreater(int(proof[0]['n']),0)
        logs.clear()
        self.assertEqual(reference_producer.run_sec(scope,'320193',selected,query,Claim(),lambda:None,database=self.database),receipt)
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
    def test_executable_market_dag_shared_aggregate_and_verified_resume(self):
        from pipelines.certified_research import indicator_state
        from tests.test_streaming_level_book import engine
        from threading import Lock
        rules=({'token_id':1,'modifier_int':0,'update_last':1,'update_high_low':1,'update_volume':1},)
        day=date(2026,8,21);unit=market.MarketUnit(str(uuid.uuid4()),day,'TEST',91,str(uuid.uuid4()))
        source_sql=(f"SELECT toUInt64({market_day_sql.bounds(day,'04:05:00')}+number*100000) sip_timestamp_us,"
            'toUInt64(number) ordinal,toUInt8(if(number=0,6,71)) event_meta,toUInt32(if(number=0,10200,10000)) price_primary_int,'
            'toUInt32(if(number=0,9800,0)) price_secondary_int,toFloat32(5) size_primary,toFloat32(20) size_secondary,'
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))+' FROM numbers(3)')
        request=producer.MarketRequest(unit,'a'*64,'b'*64,rules,{},'c'*64,'d'*64,boundaries(day)['REGULAR'],True)
        class Claim:
            def check(self):pass
        logs=[];lock=Lock()
        def factory():
            real=self.builder.Client(self.args,persistent=False)
            class Client:
                def query(self,sql,*args,**kw):
                    with lock:logs.append(sql[:100])
                    return real.query(sql,*args,**kw)
                def close(self):real.close()
            return Client()
        def start_authority():return v7.StartAuthority.genesis(engine(),request.prior_v7_hash)
        result=producer.run_market(request,factory,Claim(),lambda:None,start_authority,
                                   database=self.database,fixture_source=source_sql)
        self.assertEqual(result['status'],'complete',str(result['errors']))
        self.assertEqual(sum(s.startswith(f'INSERT INTO {self.database}.research_stage_liquidity_v1') for s in logs),1)
        bar=result['results']['bars'];technical=result['results']['technical']
        state=producer.indicator_state(self.client.query,self.database,unit,technical['attempt_id'],{},bar_attempt=bar['attempt_id'])
        self.assertEqual(set(state),set(market_day_sql.FRAMES));self.assertTrue(all(s['close']==1 for s in state.values()))
        bar_scope=Scope(unit.dataset_id,day,unit.listing_key,'bars',request.parent('bars'))
        checkpoint_scope=Scope(unit.dataset_id,day,unit.listing_key,'v7',request.parent('v7',(bar['output_hash'],)))
        checkpoint,state=v7.load_from_bars(self.client.query,self.database,Claim(),checkpoint_scope,bar_scope,
            request.dataset_hash,request.prior_v7_hash,unit.ticker,'AH',regular_close=request.regular_close)
        self.assertEqual(checkpoint['boundary'],'AH');self.assertEqual(state.ticker,unit.ticker)
        logs.clear()
        resumed=producer.run_market(request,factory,Claim(),lambda:None,start_authority,database=self.database,fixture_source=source_sql)
        self.assertEqual(resumed['status'],'complete')
        self.assertFalse(any(s.startswith('INSERT INTO') for s in logs))
        seed_scope=indicator_state.scope_for(request,technical['technical_receipt'])
        loaded=indicator_state.load(self.client.query,self.database,Claim(),seed_scope,
            request.dataset_hash,request.prior_indicator_hash)
        self.assertEqual(len(loaded['state']),8)
        with self.assertRaisesRegex(ValueError,'recipe'):
            indicator_state.load(self.client.query,self.database,Claim(),seed_scope,'f'*64,request.prior_indicator_hash)
        next_day=date(2026,8,24)
        next_unit=replace(unit,day=next_day,attempt_id=str(uuid.uuid4()))
        next_request=producer.MarketRequest(next_unit,request.dataset_hash,'e'*64,rules,loaded['state'],
            technical['output_hash'],checkpoint['state_hash'],boundaries(next_day)['REGULAR'])
        quote_sql=source_sql.replace(str(market_day_sql.bounds(day,'04:05:00')),
            str(market_day_sql.bounds(next_day,'04:05:00'))).replace('if(number=0,6,71)','6')
        quote_only=producer.run_market(next_request,factory,Claim(),lambda:None,
            lambda:v7.StartAuthority.rollover(checkpoint,next_day),database=self.database,fixture_source=quote_sql)
        self.assertEqual(quote_only['status'],'complete',str(quote_only['errors']))
        next_technical=quote_only['results']['technical']
        carried=indicator_state.load(self.client.query,self.database,Claim(),
            indicator_state.scope_for(next_request,next_technical['technical_receipt']),request.dataset_hash,technical['output_hash'])
        self.assertEqual(carried['state'],loaded['state'])
        self.assertEqual(int(next_technical['technical_receipt']['output_rows']),0)
        self.assertEqual(int(next_technical['state_receipt']['output_rows']),8)
        found=indicator_state.discover(self.client.query,self.database,Claim(),unit.listing_key,date(2026,8,25),
            request.dataset_hash,c.digest(rules),{day:request.source_hash,next_day:next_request.source_hash})
        self.assertEqual(found['session'],next_day);self.assertEqual(found['state'],loaded['state'])
        self.assertIsNone(indicator_state.discover(self.client.query,self.database,Claim(),unit.listing_key,
            date(2026,8,25),'f'*64,c.digest(rules),{}))
        with self.assertRaisesRegex(ValueError,'recipe'):
            indicator_state.discover(self.client.query,self.database,Claim(),unit.listing_key,date(2026,8,25),
                request.dataset_hash,c.digest(rules),{next_day:'0'*64})
        def competing(sql,*args,**kwargs):
            rows=self.client.query(sql,*args,**kwargs)
            return [rows[0],rows[0]] if 'min(recipe_hash)' in sql else rows
        with self.assertRaisesRegex(ValueError,'Competing'):
            indicator_state.discover(competing,self.database,Claim(),unit.listing_key,date(2026,8,25),
                request.dataset_hash,c.digest(rules),{next_day:next_request.source_hash})
        selected=indicator_state.discover(self.client.query,self.database,Claim(),unit.listing_key,date(2026,8,25),
            request.dataset_hash,c.digest(rules),{next_day:next_request.source_hash},dataset_id=unit.dataset_id)
        self.assertEqual(selected['scope'],found['scope'])
        next_scope=indicator_state.scope_for(next_request,next_technical['technical_receipt'])
        self.client.query(f'ALTER TABLE {self.database}.research_indicator_state_v1 UPDATE avg_gain=999. WHERE '
            +next_scope.where(carried['receipt']['attempt_id'])+' SETTINGS mutations_sync=2',read=False)
        with self.assertRaisesRegex(ValueError,'readback|output|receipt'):
            indicator_state.load(self.client.query,self.database,Claim(),next_scope,request.dataset_hash,technical['output_hash'])

    def test_market_campaign_global_pool_shared_scan_seal_and_resume(self):
        from tests.test_streaming_level_book import engine
        day=date(2026,8,21);dataset=str(uuid.uuid4());memory=128*1024**2
        config=c.CampaignConfig(day,day,workers=2,query_memory_bytes=memory,global_memory_bytes=2*memory)
        base=market.MarketUnit(dataset,day,'TEST',93,str(uuid.uuid4()))
        rules=({'token_id':1,'modifier_int':0,'update_last':1,'update_high_low':1,'update_volume':1},)
        request=producer.MarketRequest(base,config.content_hash,'b'*64,rules,{},'c'*64,'d'*64,
                                       boundaries(day)['REGULAR'],True)
        source_sql=(f"SELECT toUInt64({market_day_sql.bounds(day,'09:30:00')}+number*100000) sip_timestamp_us,"
            'toUInt64(number) ordinal,toUInt8(71) event_meta,toUInt32(10000) price_primary_int,'
            'toUInt32(0) price_secondary_int,toFloat32(5) size_primary,toFloat32(0) size_secondary,'
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))+' FROM numbers(3)')
        args=dict(day=str(day),listing_key=93,ticker='TEST');logs=[];lock=Lock()
        plans=tuple(PlannedUnit(Unit(key,kind,parents,memory),dict(args)) for key,kind,parents in
            (('shared','market_aggregate',()),('indicators','technical',('shared',)),('levels','v7',('shared',))))
        plans+=(PlannedUnit(Unit('prices','regular_prices',('shared',),memory),
            dict(entity_key=94,start=str(day),end=str(day))),)
        plans+=(PlannedUnit(Unit('activity','activity',('shared',),memory),dict(args,windows=(10,60))),)
        insert_rows(self.client.query,self.database,'research_membership_v1',[
            dict(dataset_id=dataset,listing_key=93,security_key=94,issuer_key=95,ticker='TEST',conid=91,
                valid_from=str(day)+' 00:00:00.000000',valid_to=str(day)+' 23:59:59.999999',
                eligible=1,reason='',evidence_hash='a'*64)])
        class Claim:
            def check(self):pass
        def factory():
            real=self.builder.Client(self.args,persistent=False)
            class Client:
                def query(self,sql,*args,**kwargs):
                    with lock:logs.append(sql[:100])
                    return real.query(sql,*args,**kwargs)
                def close(self):real.close()
            return Client()
        claim=Claim();registry=MarketHandlers(dataset,self.database,claim,factory,lambda:None,
            lambda plan,parents:request,lambda plan,parents:v7.StartAuthority.genesis(engine(),request.prior_v7_hash),fixture_source=source_sql)
        producers,verifiers=registry.registry()
        reference=ReferenceHandlers(dataset,self.database,claim,factory,lambda:None,plans)
        rp,rv=reference.registry();producers.update(rp);verifiers.update(rv)
        activity_handlers=ActivityHandlers(dataset,self.database,claim,factory,lambda:None,plans)
        ap,av=activity_handlers.registry();producers.update(ap);verifiers.update(av)
        campaign=Campaign(self.client.query,self.database,claim,dataset,config,plans,'b'*64,lambda:None,producers,verifiers)
        result=campaign.run()
        self.assertEqual(result['status'],'complete',str(result['errors']))
        self.assertTrue(result['seal']['root_hash'])
        prices=self.client.query(f"SELECT value,status FROM {self.database}.research_facts_v1 "
            f"WHERE attempt_id=toUUID('{result['results']['prices']['attempt_id']}')")
        self.assertEqual(prices,[dict(value=1.,status=1)])
        self.assertEqual(reference.received_scope(plans[-2],result['results']['prices']),
                         reference.scope(plans[-2],{'shared':result['results']['shared']}))
        self.assertGreater(int(result['results']['activity']['output_rows']),0)
        self.assertEqual(sum(sql.startswith(f'INSERT INTO {self.database}.research_stage_liquidity_v1') for sql in logs),1)
        logs.clear();resumed=campaign.run()
        self.assertEqual(resumed['seal'],result['seal'])
        self.assertFalse(any(sql.startswith('INSERT INTO') for sql in logs))
        bad=replace(plans[0],arguments=dict(args,listing_key=94))
        with self.assertRaisesRegex(ValueError,'immutable campaign scope'):registry.request(bad,{})
        with self.assertRaisesRegex(ValueError,'exact certified shared'):
            registry.verifier(plans[1],{})
        with self.assertRaisesRegex(ValueError,'exact certified shared'):
            registry.verifier(plans[1],{'shared':dict(result['results']['shared'],output_hash='f'*64)})
        with self.assertRaisesRegex(ValueError,'immutable campaign scope'):
            reference.received_scope(plans[-2],dict(result['results']['prices'],entity_key=95))
        insert_rows(self.client.query,self.database,'research_membership_v1',[
            dict(dataset_id=dataset,listing_key=93,security_key=94,issuer_key=95,ticker='TEST',conid=91,
                valid_from=str(day)+' 00:00:00.000000',valid_to=str(day)+' 23:59:59.999999',
                eligible=1,reason='',evidence_hash='b'*64)])
        with self.assertRaisesRegex(ValueError,'membership is absent or ambiguous'):campaign.run()
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import build_market_day as builder
        cls.builder=builder
        args=builder.parse_args(['--date','2026-01-02','--max-threads','2',
                                '--env-file',str(builder.ROOT/'.env')])
        cls.args=args
        cls.client=builder.Client(args,persistent=False)
        cls.database='certified_research_test_'+uuid.uuid4().hex
        cls.created=False
        try:
            s.verify_storage(cls.client.query,cls.database)
            cls.client.query('CREATE DATABASE '+cls.database,read=False)
            cls.created=True
            for ddl in (*s.ddl(cls.database),*market_day_sql.ddl(cls.database),*market.staging_ddl(cls.database),
                        reference_producer.staging_table().ddl(cls.database),float_data.staging_table().ddl(cls.database),activity.staging_table().ddl(cls.database)):
                cls.client.query(ddl,read=False)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.created:
                assert cls.database.startswith('certified_research_test_') and len(cls.database)==len('certified_research_test_')+32
                cls.client.query('DROP DATABASE '+cls.database+' SYNC',read=False)
        finally:cls.client.close()

    def test_real_ddl_roundtrip_and_actual_part_placement(self):
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4())
        self.client.query(f"INSERT INTO {self.database}.research_bars_v1 VALUES "
            f"('{dataset}','2026-01-02',1,'{attempt}',1000,14400,100000,101000,99000,100100,"
            "123.5,7,1235.1,120.5,1205.1,1,1)",read=False)
        rows=self.client.query(f"SELECT volume,trade_count,close_int FROM {self.database}.research_bars_v1 "
                               f"WHERE dataset_id=toUUID('{dataset}') AND attempt_id=toUUID('{attempt}')")
        self.assertEqual(rows,[dict(volume=123.5,trade_count=7,close_int=100100)])
        s.verify_storage(self.client.query,self.database,require_tables=True)
        columns=self.client.query(f"SELECT name,compression_codec FROM system.columns "
            f"WHERE database='{self.database}' AND table='research_bars_v1'")
        self.assertTrue(columns)
        self.assertTrue(all('ZSTD' in r['compression_codec'] for r in columns))

    def test_publication_reuses_verified_attempt_and_rejects_changed_outputs(self):
        # Isolated fixture ownership; production receives a KeeperClaim.
        database=self.database
        class FixtureClaim:
            def check(self):
                if not database.startswith('certified_research_test_'):raise ValueError('Fixture scope')
        publisher=Publisher(self.client.query,self.database,FixtureClaim())
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4())
        scope=Scope(dataset,date(2026,1,2),11,'bars','c'*64)
        row=dict(dataset_id=dataset,session_date='2026-01-02',listing_key=11,attempt_id=attempt,
                 resolution_ms=1000,bucket_index=14400,open_int=100000,high_int=101000,
                 low_int=99000,close_int=100100,volume=123.5,trade_count=7,notional=1235.1,
                 execution_volume=120.5,execution_notional=1205.1,price_valid=1,extremes_valid=1)
        insert_rows(self.client.query,self.database,'research_bars_v1',[row])
        expected=publisher.evidence(scope,attempt) # Fixture independently asserts all inserted values above.
        with self.assertRaisesRegex(ValueError,'expected evidence'):publisher.publish(scope,attempt)
        first=publisher.publish(scope,attempt,expected=expected)
        self.assertEqual(publisher.publish(scope,attempt),first)
        with self.assertRaisesRegex(ValueError,'dependencies'):
            publisher.receipt(replace(scope,parent_hash='d'*64))
        insert_rows(self.client.query,self.database,'research_bars_v1',[dict(row,close_int=100101)])
        with self.assertRaisesRegex(ValueError,'Duplicate'):publisher.receipt(scope)
        empty=Scope(dataset,date(2026,1,2),12,'bars','e'*64)
        empty_attempt=str(uuid.uuid4());empty_expected=publisher.evidence(empty,empty_attempt)
        with self.assertRaisesRegex(ValueError,'Empty'):publisher.publish(empty,empty_attempt,expected=empty_expected)
        attempt2=str(uuid.uuid4())
        receipt=publisher.publish(empty,attempt2,expected=publisher.evidence(empty,attempt2),empty_proof='f'*64)
        self.assertEqual(int(receipt['status']),2)

    def test_normalized_identity_and_half_open_historical_membership(self):
        from datetime import timezone,timedelta
        class Claim:
            def check(self):pass
        claim=Claim();dataset=str(uuid.uuid4())
        key=ensure_identity(self.client.query,self.database,claim,LISTING,'listing:fixture')
        self.assertEqual(ensure_identity(self.client.query,self.database,claim,LISTING,'listing:fixture'),key)
        bulk=ensure_identities(self.client.query,self.database,claim,((LISTING,'listing:fixture'),(LISTING,'listing:second')))
        self.assertEqual(bulk[(LISTING,'listing:fixture')],key)
        start=datetime(2026,1,2,9,tzinfo=timezone.utc);end=start+timedelta(days=3)
        active=ListingInterval('listing:fixture','security:fixture','issuer:fixture','IPO',321,start,end,
                               'US','NASDAQ','USD','STK',True,True,False,False,'a'*64)
        dead=replace(active,listed=False,valid_from=end,valid_to=end+timedelta(days=10))
        insert_rows(self.client.query,self.database,'research_membership_v1',[active.row(dataset),dead.row(dataset)])
        self.assertFalse(membership_at(self.client.query,self.database,dataset,start-timedelta(microseconds=1)))
        self.assertEqual(len(membership_at(self.client.query,self.database,dataset,start)),1)
        self.assertFalse(membership_at(self.client.query,self.database,dataset,end))

    def test_sec_duration_context_recovered_from_retained_mirror_without_files(self):
        selected=tuple(m for m in facts.catalog() if m.name=='fundamental.operating_cash_flow')
        sql=reference_sql.sec_projection(str(uuid.uuid4()),str(uuid.uuid4()),992,'320193',
                                        date(2026,1,1),date(2026,8,21),metrics=selected)
        rows=self.client.query(f'SELECT * FROM ({sql}) WHERE context_kind=2 LIMIT 3')
        self.assertTrue(rows)
        self.assertTrue(all(r['period_start'] is not None and r['period_start']<=r['period_end'] for r in rows))
        self.assertTrue(all(r['reason']=='' for r in rows))

    def test_public_asof_facts_preserve_original_vintage_until_revision(self):
        from datetime import timezone
        dataset=str(uuid.uuid4());metric=facts.catalog()[0].key;attempt=str(uuid.uuid4())
        base=dict(dataset_id=dataset,attempt_id=attempt,entity_kind=1,entity_key=19,metric_key=metric,period_start=None,
                  period_end='2025-12-31',context_kind=0,unit_key=1,status=1,precision=1,confidence=100,
                  reason='',evidence_hash='a'*64)
        rows=[dict(base,available_at='2026-02-10 12:00:00.000000',revision=0,value=100.),
              dict(base,available_at='2026-03-01 12:00:00.000000',revision=1,value=110.)]
        insert_rows(self.client.query,self.database,'research_facts_v1',rows)
        class Claim:
            def check(self):pass
        scope=ReferenceScope(dataset,1,19,'fundamentals',date(2026,1,1),date(2026,3,2),'a'*64,facts.catalog_hash())
        pub=ReferencePublisher(self.client.query,self.database,Claim())
        sql=facts.lookup_sql(self.database,dataset,[(1,19)],[metric],datetime(2026,2,11,tzinfo=timezone.utc))
        self.assertFalse(self.client.query(sql)) # Rows alone do not establish authority.
        pub.publish(scope,attempt,pub.evidence(scope,attempt))
        def read(at,strict=False):
            return self.client.query(facts.lookup_sql(self.database,dataset,[(1,19)],[metric],at,strict_prior=strict))
        self.assertFalse(read(datetime(2026,1,15,tzinfo=timezone.utc)))
        self.assertEqual(read(datetime(2026,2,11,tzinfo=timezone.utc))[0]['value'],100.)
        boundary=datetime(2026,3,1,12,tzinfo=timezone.utc)
        self.assertEqual(read(boundary,True)[0]['value'],100.)
        self.assertEqual(read(boundary)[0]['value'],110.)

    def test_causal_v7_binary_payload_roundtrip_in_clickhouse(self):
        from tests.test_streaming_level_book import engine,bars
        a=engine();binding=CheckpointBinding('a'*64,'b'*64,'c'*64,'TEST',date(2026,8,21))
        outputs,_=v7.generate(a,bars(a),binding)
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4())
        self.client.query(v7.insert_sql(outputs,dataset,attempt,81,binding.session,self.database),read=False)
        rows=self.client.query(f'SELECT boundary,state_hash,hex(payload) payload_hex '
            f"FROM {self.database}.research_v7_checkpoint_v1 WHERE listing_key=81 ORDER BY boundary")
        self.assertEqual(len(rows),3)
        for row,output in zip(rows,outputs):
            self.assertEqual(row['state_hash'],output['state_hash'])
            state=restore(bytes.fromhex(row['payload_hex']),row['state_hash'],output['binding'])
            self.assertEqual(state.checkpoint(),restore(output['payload'],output['state_hash'],output['binding']).checkpoint())
        class Claim:
            def check(self):pass
        publisher=Publisher(self.client.query,self.database,Claim())
        expected=self.client.query(v7.expected_evidence_sql(outputs,dataset,attempt,81,binding.session))[0]
        scope=Scope(dataset,binding.session,81,'v7','d'*64)
        publisher.publish(scope,attempt,expected={k:int(v) for k,v in expected.items()})
        for output in outputs:
            loaded,state=v7.load_checkpoint(self.client.query,self.database,Claim(),scope,output['binding'],output['boundary'])
            self.assertEqual(loaded['payload'],output['payload'])
            self.assertEqual(state.checkpoint(),restore(output['payload'],output['state_hash'],output['binding']).checkpoint())
        with self.assertRaisesRegex(ValueError,'binding mismatch'):
            v7.load_checkpoint(self.client.query,self.database,Claim(),scope,
                replace(outputs[0]['binding'],input_prefix_hash='f'*64),'PM')
        bad=[dict(o) for o in outputs];bad[1]['parent_hash']='e'*64
        with self.assertRaisesRegex(ValueError,'chain'):v7.insert_sql(bad,dataset,str(uuid.uuid4()),81,binding.session,self.database)

    def test_real_training_minute_strict_activity_matches_canonical_events(self):
        # Bounded, public training date only. No full-day source scan or final
        # research publication is performed by this pilot fixture.
        day=date(2026,8,21)
        source=(f"SELECT * FROM ({market_day_sql.canonical_source(day,'AAPL')}) "
                f"WHERE sip_timestamp_us>={market_day_sql.bounds(day,'09:30:00')} "
                f"AND sip_timestamp_us<{market_day_sql.bounds(day,'09:31:00')}")
        rules=self.client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume "
            "FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' "
            "AND is_join_canonical=1 ORDER BY token_id")
        self.assertTrue(rules)
        unit=market.MarketUnit(str(uuid.uuid4()),day,'AAPL',77,str(uuid.uuid4()))
        self.client.query(market.aggregate_sql(unit,rules,self.database,source=source),read=False)
        for query in market.bar_queries(unit,self.database):self.client.query(query,read=False)
        self.client.query(market.normalized_insert(unit,'broker_100ms',self.database),read=False)
        form,_,_,volume=market_day_sql.condition_expressions(rules)
        expected=self.client.query("SELECT sumIf(toFloat64(size_primary),kind=1 AND bitAnd(event_meta,192)=64 "
            f"AND price_primary_int>0 AND size_primary>0 AND isFinite(size_primary) AND {volume}) volume,"
            f"countIf(kind=1 AND bitAnd(event_meta,192)=64 AND price_primary_int>0 AND size_primary>0 "
            f"AND isFinite(size_primary) AND {volume}) trades FROM (SELECT *,bitAnd(event_meta,1) kind,"
            "toHour(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York'))*3600+"
            "toMinute(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York'))*60+"
            "toSecond(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York')) local_second,"
            "arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),"
            "toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) tokens,"
            f"({form}) form_ok FROM ({source}))")[0]
        actual=self.client.query(f"SELECT sum(volume) volume,sum(trade_count) trades "
                                 f"FROM {self.database}.research_liquidity_v1 WHERE listing_key=77")[0]
        self.assertGreater(int(actual['trades']),0)
        self.assertEqual(int(actual['trades']),int(expected['trades']))
        self.assertAlmostEqual(actual['volume'],expected['volume'],places=6)

    def test_strict_trade_gate_matches_real_sql_volume_price_and_count(self):
        day=date(2026,1,2);attempt=str(uuid.uuid4())
        rows=[(0,6,10200,9800,20.),(1,71,10000,0,5.),(2,7,990000,0,30.),
              (3,199,880000,0,40.),(4,135,770000,0,50.)]
        source=' UNION ALL '.join(
            f"SELECT toUInt64({market_day_sql.bounds(day,'04:05:00')}+{offset*100000}) sip_timestamp_us,"
            f"toUInt64({offset}) ordinal,toUInt8({meta}) event_meta,"
            f"toUInt32({price}) price_primary_int,toUInt32({bid}) price_secondary_int,"
            f"toFloat32({size}) size_primary,toFloat32(20) size_secondary,"
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))
            for offset,meta,price,bid,size in rows)
        self.client.query(market_day_sql.broker_sql(self.database,'fixture',day,'STRICT',attempt,[],source,
                          verified_timely=True),read=False)
        result=self.client.query(f"SELECT sum(volume) volume,sum(trade_count) trades,maxIf(high_int,extremes_valid) high,"
            f"sum(quote_event_count) quotes FROM {self.database}.liquidity_100ms_v1 WHERE ticker='STRICT'")
        self.assertEqual(result,[dict(volume=5,trades=1,high=10000,quotes=1)])
        # The actual new normalized path reuses the same aggregate for rollups
        # and indicators; source rows do not leave ClickHouse.
        unit=market.MarketUnit(str(uuid.uuid4()),day,'STRICT',7,str(uuid.uuid4()))
        self.client.query(market.aggregate_sql(unit,[],self.database,source=source),read=False)
        for query in market.bar_queries(unit,self.database):self.client.query(query,read=False)
        self.client.query(market.indicator_sql(unit,self.database),read=False)
        for product in ('bars','technical','broker_100ms'):
            self.client.query(market.normalized_insert(unit,product,self.database),read=False)
            class Claim:
                def check(self):pass
            publisher=Publisher(self.client.query,self.database,Claim())
            scope=Scope(unit.dataset_id,day,unit.listing_key,product,'a'*64)
            expected=self.client.query(market.expected_evidence_sql(unit,product,self.database))[0]
            publisher.publish(scope,unit.attempt_id,expected={k:int(v) for k,v in expected.items()})
        result=self.client.query(f"SELECT sum(volume) volume,sum(trade_count) trades,"
            "sum(reporting_delayed_trades) delayed,sum(reporting_unknown_trades) unknown,"
            f"sum(reporting_invalid_trades) invalid FROM {self.database}.research_liquidity_v1 WHERE listing_key=7")
        self.assertEqual(result,[dict(volume=5,trades=1,delayed=1,unknown=1,invalid=1)])
        result=self.client.query(f"SELECT resolution_ms,volume,trade_count FROM {self.database}.research_bars_v1 "
                                 "WHERE listing_key=7 ORDER BY resolution_ms")
        self.assertEqual([r['resolution_ms'] for r in result],list(market_day_sql.FRAMES))
        self.assertTrue(all(r['volume']==5 and r['trade_count']==1 for r in result))
        indicators=self.client.query(f"SELECT ema_7 FROM {self.database}.research_indicators_v1 WHERE listing_key=7")
        self.assertEqual(indicators,[dict(ema_7=1)]*8)
        s.verify_storage(self.client.query,self.database,require_tables=True)


if __name__=='__main__':unittest.main()
