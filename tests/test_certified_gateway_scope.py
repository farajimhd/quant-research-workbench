from unittest.mock import Mock,patch
import pytest
from services.reference_gateway import ticker_events as gateway


def entity_row():
    return dict(provider_entity_key='entity',provider_identifier='BBG000000001',current_ticker='TEST')


def test_selected_inventory_pushes_scope_and_bound_to_clickhouse():
    with patch.object(gateway,'query_json_each_row',return_value=[entity_row()]) as read:
        rows=gateway.load_ticker_event_inventory(Mock(),database='q_live',only_identifiers=['bbg000000001'])
    assert len(rows)==1 and rows[0].provider_entity_key=='entity'
    sql=read.call_args.args[1]
    assert "upper(provider_identifier) IN ('BBG000000001')" in sql and 'LIMIT 1001' in sql


@pytest.mark.parametrize('rows',[[dict(entity_row(),provider_identifier='other',current_ticker='OTHER')],
    [entity_row(),entity_row()], [entity_row() for _ in range(1001)]])
def test_selected_inventory_rejects_unbounded_or_unrequested_readback(rows):
    with patch.object(gateway,'query_json_each_row',return_value=rows),pytest.raises(ValueError,match='bound|scope'):
        gateway.load_ticker_event_inventory(Mock(),database='q_live',only_identifiers=['BBG000000001'])


def test_empty_selected_entity_sets_do_not_scan_coverage_or_integrity():
    with patch.object(gateway,'query_json_each_row') as read:
        assert gateway.load_ticker_event_coverage(Mock(),database='q_live',entity_keys=[])=={}
        assert gateway.load_integrity_repair_entity_keys(Mock(),database='q_live',entity_keys=[])==set()
    read.assert_not_called()


def test_unscoped_gateway_reads_keep_existing_full_inventory_contract():
    with patch.object(gateway,'query_json_each_row',return_value=[]) as read:
        gateway.load_ticker_event_inventory(Mock(),database='q_live')
    assert 'LIMIT' not in read.call_args.args[1] and ' IN (' not in read.call_args.args[1]
    with pytest.raises(ValueError,match='collection'):
        gateway.bounded_reference_keys('BBG000000001')


@pytest.mark.parametrize('loader',[gateway.load_ticker_event_coverage,gateway.load_integrity_repair_entity_keys])
def test_selected_child_metadata_is_scoped_and_readback_checked(loader):
    with patch.object(gateway,'query_json_each_row',return_value=[dict(provider_entity_key='entity')]) as read:
        loader(Mock(),database='q_live',entity_keys=['entity'])
    assert "WHERE provider_entity_key IN ('entity')" in read.call_args.args[1]
    assert 'LIMIT 1001' in read.call_args.args[1]
    with patch.object(gateway,'query_json_each_row',return_value=[dict(provider_entity_key='unexpected')]),\
         pytest.raises(ValueError,match='scope'):
        loader(Mock(),database='q_live',entity_keys=['entity'])


def test_scoped_sync_uses_selected_metadata_and_reuses_completed_history_without_provider():
    entity=gateway.load_ticker_event_inventory
    with patch.object(gateway,'query_json_each_row',return_value=[entity_row()]):
        inventory=entity(Mock(),database='q_live',only_identifiers=['BBG000000001'])
    provider=Mock()
    with patch.object(gateway,'load_ticker_event_corrections',return_value=[]),\
         patch.object(gateway,'load_ticker_event_inventory',return_value=inventory) as inv,\
         patch.object(gateway,'load_ticker_event_coverage',return_value={'entity':dict(source_status='completed',mapping_status='mapped')}) as coverage,\
         patch.object(gateway,'load_integrity_repair_entity_keys',return_value=set()) as repairs,\
         patch.object(gateway,'load_canonical_bindings',return_value={}),\
         patch.object(gateway,'load_ticker_bindings',return_value={}),\
         patch.object(gateway,'load_existing_rows',return_value={}):
        result=gateway.sync_ticker_events(Mock(),provider,database='q_live',execute=False,
            mode='historical',only_identifiers=(value for value in ['BBG000000001']))
    assert result.inventory_entities==1 and result.selected_entities==0
    assert inv.call_args.kwargs['only_identifiers']==('BBG000000001',)
    assert coverage.call_args.kwargs['entity_keys']==repairs.call_args.kwargs['entity_keys']==('entity',)
    provider.fetch_ticker_events.assert_not_called()


def test_oversized_request_fails_before_writes_or_provider():
    client=Mock();provider=Mock()
    with patch.object(gateway,'publish_ticker_event_corrections') as publish,pytest.raises(ValueError,match='1000'):
        gateway.sync_ticker_events(client,provider,database='q_live',execute=True,
            only_identifiers=(str(index) for index in range(1001)))
    publish.assert_not_called();provider.fetch_ticker_events.assert_not_called()


def test_selected_inventory_failure_cannot_publish_corrections():
    with patch.object(gateway,'load_ticker_event_corrections',return_value=[]),\
         patch.object(gateway,'load_ticker_event_inventory',side_effect=ValueError('Selected inventory exceeds scope')),\
         patch.object(gateway,'publish_ticker_event_corrections') as publish,pytest.raises(ValueError,match='scope'):
        gateway.sync_ticker_events(Mock(),Mock(),database='q_live',execute=True,only_identifiers=['BBG000000001'])
    publish.assert_not_called()
