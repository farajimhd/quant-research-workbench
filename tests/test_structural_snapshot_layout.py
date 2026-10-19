import json
import os
import uuid
from types import SimpleNamespace
import pytest
from scripts.repair_structural_snapshot_layout import reordered_ddl,fingerprint
from pipelines.market_sip.events import structural_checkpoint_storage as storage


def ddl():
    return """CREATE TABLE arte.structural_levels_v7 (
      ticker String,level_id String,valid_from DateTime64(9,'UTC'),
      checkpoint_boundary LowCardinality(String),row_revision UInt64,
      price Float64,optional Nullable(Float64))
      ENGINE=ReplacingMergeTree(row_revision) PARTITION BY cityHash64(ticker)%64
      PRIMARY KEY (ticker,level_id,valid_from)
      ORDER BY (ticker,level_id,valid_from,checkpoint_boundary)
      SETTINGS storage_policy='live_market_ssd'"""


def test_only_physical_key_order_changes():
    value=reordered_ddl(ddl(),storage.LEVELS_TABLE,'arte.test_snapshot_shadow',storage.SNAPSHOT_KEYS[storage.LEVELS_TABLE])
    assert 'PRIMARY KEY (ticker, valid_from, checkpoint_boundary)' in value
    assert 'ORDER BY (ticker, valid_from, checkpoint_boundary, level_id)' in value
    assert 'ReplacingMergeTree(row_revision)' in value
    assert "storage_policy='live_market_ssd'" in value
    with pytest.raises(ValueError,match='membership'):
        reordered_ddl(ddl(),storage.LEVELS_TABLE,'arte.shadow','ticker, valid_from')
    with pytest.raises(ValueError,match='partition'):
        fingerprint(None,'arte.shadow','0; DROP TABLE test')


def test_real_snapshot_reorder_preserves_bits_versions_and_atomic_rollback():
    endpoint=os.environ.get('STRUCTURAL_LAYOUT_TEST_CH_URL')
    if not endpoint:pytest.skip('Set STRUCTURAL_LAYOUT_TEST_CH_URL for isolated SSD integration')
    from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_user,default_clickhouse_password
    http=ClickHouseHttpClient(endpoint,default_clickhouse_user(),default_clickhouse_password())
    client=SimpleNamespace(query=lambda q:[json.loads(r) for r in http.execute(q+' FORMAT JSONEachRow').splitlines()],http=http)
    database='structural_layout_test_'+uuid.uuid4().hex[:12]
    http.execute(f'CREATE DATABASE {database}')
    try:
        old=database+'.old';new=database+'.new'
        http.execute(ddl().replace(storage.LEVELS_TABLE,old))
        http.execute(reordered_ddl(ddl(),storage.LEVELS_TABLE,new,storage.SNAPSHOT_KEYS[storage.LEVELS_TABLE]))
        http.execute(f"""INSERT INTO {old} VALUES
            ('TEST','one','2026-01-02 14:30:00.000000001','PM',1,2.97,NULL),
            ('TEST','one','2026-01-02 14:30:00.000000001','PM',2,3.01,-0.0),
            ('TEST','two','2026-01-02 14:30:00.000000002','REGULAR',1,2.9700000000000002,NULL)""")
        part=client.query(f"SELECT DISTINCT _partition_id p FROM {old}")[0]['p']
        before=fingerprint(client,old,part)
        assert int(before['rows'])==2
        http.execute(f'INSERT INTO {new} SELECT * FROM {old} FINAL')
        assert fingerprint(client,new,part)==before
        original=client.query(f"SELECT uuid FROM system.tables WHERE database='{database}' AND name='old'")[0]['uuid']
        http.execute(f'EXCHANGE TABLES {old} AND {new}')
        assert fingerprint(client,old,part)==before
        assert client.query(f"SELECT uuid FROM system.tables WHERE database='{database}' AND name='new'")[0]['uuid']==original
        http.execute(f"INSERT INTO {old} VALUES ('TEST','one','2026-01-02 14:30:00.000000001','PM',3,3.02,-0.0)")
        assert fingerprint(client,old,part)!=before
    finally:
        http.execute(f'DROP DATABASE {database} SYNC')
