from __future__ import annotations

from datetime import UTC, date, datetime
import os
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.reference_gateway import tradable_snapshots as S


class SessionAssignment(unittest.TestCase):
    def test_after_close_and_weekend_target_next_open(self):
        self.assertEqual(S.target_session(datetime(2026,8,21,0,21,tzinfo=UTC)),date(2026,8,21))
        self.assertEqual(S.target_session(datetime(2026,8,29,2,31,tzinfo=UTC)),date(2026,8,31))
        self.assertEqual(S.target_session(datetime(2026,9,2,2,8,tzinfo=UTC)),date(2026,9,2))

    def test_holiday_and_dst_cutoff(self):
        self.assertEqual(S.target_session(datetime(2026,9,7,22,0,tzinfo=UTC)),date(2026,9,8))
        self.assertEqual(S.target_session(datetime(2026,11,2,8,59,tzinfo=UTC)),date(2026,11,2))
        self.assertEqual(S.target_session(datetime(2026,11,2,9,0,tzinfo=UTC)),date(2026,11,3))


@unittest.skipUnless(os.environ.get('MARKET_DAY_CLICKHOUSE_TEST')=='1','ClickHouse fixture is opt-in')
class SnapshotFixture(unittest.TestCase):
    def test_retained_publication_copy_and_certificate(self):
        from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[1]))
        db='tradable_snapshot_test_'+uuid.uuid4().hex
        client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password())
        try:
            client.execute(f'CREATE DATABASE {db}')
            client.execute(f'''CREATE TABLE {db}.feature_tradable_universe_v1 (
                universe_date Date,ticker String,symbol_id String,listing_id String,security_id String,
                is_tradable UInt8,exclusion_reason Nullable(String),source_run_id String,
                inserted_at DateTime64(3,'UTC')) ENGINE=ReplacingMergeTree(inserted_at)
                PARTITION BY toYYYYMM(universe_date) ORDER BY (universe_date,ticker,listing_id)
                SETTINGS storage_policy='live_market_ssd' ''')
            client.execute(f'''INSERT INTO {db}.feature_tradable_universe_v1 VALUES
                ('2026-08-29','AAA','s1','l1','sec1',1,NULL,'retained',toDateTime64('2026-08-29 02:31:14.300',3,'UTC')),
                ('2026-08-29','BBB','s2','l2','sec2',0,'inactive_listing','retained',toDateTime64('2026-08-29 02:31:14.300',3,'UTC'))''')
            available=datetime(2026,8,29,2,31,17,tzinfo=UTC)
            with self.assertRaisesRegex(ValueError,'publication was outside'):
                S.publish_retained_snapshot(client,db,date(2026,8,29),
                    available_at_utc=datetime(2026,8,31,8,1,tzinfo=UTC))
            result=S.publish_retained_snapshot(client,db,date(2026,8,29),
                available_at_utc=available,expected_session=date(2026,8,31))
            self.assertEqual((result['session_date'],result['rows'],result['tradable']),('2026-08-31',2,1))
            self.assertEqual(S.publish_retained_snapshot(client,db,date(2026,8,29),available_at_utc=available),result)
            cert=S.query(client,f"SELECT row_count,tradable_count,source_hash FROM {db}.{S.COVERAGE} FINAL")
            self.assertEqual((cert[0]['row_count'],cert[0]['tradable_count']),(2,1))
            gaps=S.record_missing_sessions(client,db,date(2026,8,31),date(2026,9,2))
            self.assertEqual(gaps,['2026-09-01','2026-09-02'])
            states=S.query(client,f"SELECT session_date,status FROM {db}.{S.COVERAGE} FINAL ORDER BY session_date")
            self.assertEqual([row['status'] for row in states],
                ['certified','unresolved_no_preopen_capture','unresolved_no_preopen_capture'])
            parts=S.query(client,f"SELECT distinct disk_name FROM system.parts WHERE active AND database='{db}'")
            self.assertEqual(parts,[{'disk_name':'live_market_ssd'}])
        finally:
            if db.startswith('tradable_snapshot_test_') and len(db)==55:
                client.execute(f'DROP DATABASE IF EXISTS {db} SYNC')
