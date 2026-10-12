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
from pipelines.certified_research import market
from pipelines.certified_research.publication import Scope,Publisher,KeeperClaim,insert_rows
from pipelines.certified_research.identity import ListingInterval,validate_intervals,identity_key,ensure_identity,membership_at,LISTING
from pipelines.certified_research import facts
from pipelines.certified_research import source,acquisition
from pipelines.certified_research import v7
from pipelines.certified_research import context


class Contracts(unittest.TestCase):
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
        reporting=acquisition.reporting_command(paths,start,end,hydrate=True)
        self.assertIn('--hydrate-missing-trades',reporting)
        self.assertEqual(acquisition.contiguous_ranges([end,start,start]),((start,start),(end,end)))
        self.assertTrue(str(paths.cache).startswith('\\\\DESKTOP-SAAI85T\\Workstation-D'))
        from unittest.mock import patch
        from pipelines.market_sip.flatfiles import download_update_events as updater
        from scripts import backfill_trade_reporting_flags as migration
        with patch.object(sys,'argv',list(cmd[2:])):
            parsed=updater.parse_args()
        self.assertTrue(parsed.skip_bars);self.assertEqual(parsed.env_file,[paths.env_file])
        self.assertTrue(migration.parse_args(list(reporting[3:])).hydrate_missing_trades)
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
        row=dict(dataset_id=str(uuid.uuid4()),entity_kind=1,entity_key=1,metric_key=2,revision=0,
                 available_at=at,evidence_hash='a'*64,confidence=100,status=1,precision=2,unit_key=1,value=123.)
        facts.verify_fact(row)
        for fields in (dict(available_at=at.replace(tzinfo=None)),dict(value=float('nan')),dict(status=2),dict(evidence_hash='missing')):
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
        client=Client();claim=KeeperClaim(client,str(uuid.uuid4()),owner='owner')
        claim.check();client.client_id=(2,b'replaced')
        with self.assertRaisesRegex(RuntimeError,'expired'):claim.check()
        claim.close();self.assertTrue(client.lock.is_acquired)

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
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import build_market_day as builder
        cls.builder=builder
        args=builder.parse_args(['--date','2026-01-02','--max-threads','2',
                                '--env-file',str(builder.ROOT/'.env')])
        cls.client=builder.Client(args,persistent=False)
        cls.database='certified_research_test_'+uuid.uuid4().hex
        cls.created=False
        try:
            s.verify_storage(cls.client.query,cls.database)
            cls.client.query('CREATE DATABASE '+cls.database,read=False)
            cls.created=True
            for ddl in (*s.ddl(cls.database),*market_day_sql.ddl(cls.database),*market.staging_ddl(cls.database)):
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
        start=datetime(2026,1,2,9,tzinfo=timezone.utc);end=start+timedelta(days=3)
        active=ListingInterval('listing:fixture','security:fixture','issuer:fixture','IPO',321,start,end,
                               'US','NASDAQ','USD','STK',True,True,False,False,'a'*64)
        dead=replace(active,listed=False,valid_from=end,valid_to=end+timedelta(days=10))
        insert_rows(self.client.query,self.database,'research_membership_v1',[active.row(dataset),dead.row(dataset)])
        self.assertFalse(membership_at(self.client.query,self.database,dataset,start-timedelta(microseconds=1)))
        self.assertEqual(len(membership_at(self.client.query,self.database,dataset,start)),1)
        self.assertFalse(membership_at(self.client.query,self.database,dataset,end))

    def test_public_asof_facts_preserve_original_vintage_until_revision(self):
        from datetime import timezone
        dataset=str(uuid.uuid4());metric=facts.catalog()[0].key
        base=dict(dataset_id=dataset,entity_kind=1,entity_key=19,metric_key=metric,period_start=None,
                  period_end='2025-12-31',unit_key=1,status=1,precision=1,confidence=100,
                  reason='',evidence_hash='a'*64)
        rows=[dict(base,available_at='2026-02-10 12:00:00.000000',revision=0,value=100.),
              dict(base,available_at='2026-03-01 12:00:00.000000',revision=1,value=110.)]
        insert_rows(self.client.query,self.database,'research_facts_v1',rows)
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
