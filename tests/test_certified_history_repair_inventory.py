import pytest
from pipelines.certified_research.universe import history_repair_inventory


def snapshot():
    return dict(source_hash='a'*64,rejected=tuple(dict(listing_id=str(i),reason='missing_dated_symbol_mapping') for i in range(4)))


def test_missing_empty_conflicting_and_completed_sources_remain_distinct():
    rows=[dict(listing_id=str(i),records=1,coverage=[('entity',status,mapping,0,'b'*64)])
        for i,status,mapping in ((1,'covered_empty','mapped'),(2,'completed','source_conflict'),(3,'completed','mapped'))]
    result=history_repair_inventory(lambda sql:rows,snapshot())
    assert result['inspection_only'] and result['listings']==4
    assert result['lanes']==dict(missing_retained_history_coverage=1,prove_empty_history_symbol_origin=1,
        resolve_source_mapping_conflict=1,rebuild_intervals_from_retained_history=1)
    assert history_repair_inventory(lambda sql:list(reversed(rows)),snapshot())==result


def test_duplicate_metadata_and_scope_overflow_are_rejected():
    row=dict(listing_id='1',records=1,coverage=[('entity','completed','mapped',0,'b'*64)])
    with pytest.raises(ValueError,match='Competing'):history_repair_inventory(lambda sql:[row,row],snapshot())
    with pytest.raises(ValueError,match='bound'):history_repair_inventory(lambda sql:[],snapshot(),maximum_listings=1)
