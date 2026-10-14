from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from pipelines.sec.edgar import sec_bulk_clickhouse_ingest as ingest,sec_bulk_snapshot_refresh as bulk
from pipelines.sec.edgar import sec_acceptance_archive_repair as acceptance


def test_all_bulk_mirror_ddl_has_explicit_lossless_ssd_default():
    for builder in (ingest.raw_source_file_table_sql,ingest.company_table_sql,ingest.ticker_table_sql,
            ingest.submission_file_ref_table_sql,ingest.filing_table_sql,ingest.xbrl_fact_table_sql,
            ingest.snapshot_manifest_table_sql):
        sql=builder('fixture','')
        assert "storage_policy = 'live_market_ssd'" in sql and "default_compression_codec = 'ZSTD(1)'" in sql
    with pytest.raises(ValueError,match='requires live_market_ssd'):ingest.merge_tree_settings('default')


@pytest.mark.parametrize('builder',[
    lambda client:ingest.create_database_and_tables(client,'fixture',''),
    lambda client:bulk.create_replacement_tables(client,'fixture',['base'],'run',''),
    lambda client:bulk.create_raw_stage(client,'fixture','raw',''),
    lambda client:bulk.refresh_selected_snapshots(client,SimpleNamespace(database='fixture',storage_policy=''),
        [SimpleNamespace(source_name='companyfacts')],'run',None,None),
    lambda client:acceptance.cleanup_superseded_fallbacks(client,SimpleNamespace(database='fixture',target_table='filing'),[],[],'run'),
])
def test_misplaced_inputs_block_before_any_ddl_or_payload(builder):
    client=Mock()
    with patch.object(ingest,'verify_sec_write_storage',side_effect=ValueError('misplaced')),\
         patch.object(bulk,'verify_sec_write_storage',side_effect=ValueError('misplaced')),\
         patch.object(acceptance,'verify_sec_write_storage',side_effect=ValueError('misplaced')),\
         pytest.raises(ValueError,match='misplaced'):
        builder(client)
    client.execute.assert_not_called()


def test_snapshot_cutover_checks_parts_before_exchange():
    client=Mock()
    with patch.object(bulk,'verify_sec_write_storage',side_effect=ValueError('misplaced parts')),\
         pytest.raises(ValueError,match='parts'):
        bulk.validate_and_cut_over(client,SimpleNamespace(database='fixture'),['base'],{'base':1},run_id='run')
    client.execute.assert_not_called()
