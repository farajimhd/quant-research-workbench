from src.trading_runtime.arte_native_channel_exit_schema import DECISION,SOURCE_ROW,EXECUTION_ROW,TABLES


def test_channel_exit_evidence_has_normalized_source_and_execution_families_on_ssd():
    assert len({table.name for table in TABLES})==3
    for table in TABLES:
        ddl=table.ddl()
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert 'batch_id_bloom_v1' in ddl
        assert {'record_id','parent_record_id','run_id','batch_id','content_hash'}<=dict(table.columns).keys()
    header=dict(DECISION.columns)
    assert header['first_execution_time']=="DateTime64(6, 'UTC')"
    assert header['acquisition_day_us']=='UInt64'
    assert header['position_quantity']=='Decimal(38, 10)'
    assert {'source_row_count','execution_row_count','source_feature_plan_token',
        'source_manager_checkpoint_sequence','source_broker_snapshot_hash'}<=header.keys()
    assert dict(SOURCE_ROW.columns)['decision_record_id']=='UUID'
    assert dict(EXECUTION_ROW.columns)['decision_record_id']=='UUID'
