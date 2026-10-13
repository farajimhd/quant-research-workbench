from datetime import date
from unittest.mock import Mock
from uuid import uuid4
from dataclasses import replace

import pytest

from pipelines.certified_research.source import require_exact_symbol


def test_exact_and_genuinely_absent_symbols_do_not_trigger_fallback():
    query=Mock(return_value=[dict(ticker='TEST',event_count=5)])
    assert require_exact_symbol(query,date(2026,1,2),'TEST')
    assert 'events_ticker_day_index' in query.call_args.args[0] and 'events_2026' not in query.call_args.args[0]
    query.return_value=[]
    assert not require_exact_symbol(query,date(2026,1,2),'ABSENT')


@pytest.mark.parametrize('rows',([dict(ticker='ABRpD',event_count=10)],
    [dict(ticker='ABRPD',event_count=10),dict(ticker='ABRpD',event_count=10)],
    [dict(ticker='ABRPD',event_count=0)]))
def test_mismatch_ambiguity_or_invalid_index_blocks_before_event_scan(rows):
    with pytest.raises(ValueError):require_exact_symbol(lambda sql:rows,date(2026,1,2),'ABRPD')


def test_explicit_spelling_selects_raw_symbol_but_keeps_identity_and_binds_lineage():
    from pipelines.certified_research.market import MarketUnit,aggregate_sql
    from pipelines.certified_research.producer import MarketRequest
    from pipelines.certified_research.checkpoints import boundaries
    day=date(2026,1,2);query=Mock(return_value=[dict(ticker='ABRpD',event_count=10)])
    assert require_exact_symbol(query,day,'ABRPD',source_ticker='ABRpD')
    unit=MarketUnit(str(uuid4()),day,'ABRPD',1,str(uuid4()),'ABRpD')
    rules=({'token_id':1,'modifier_int':0,'update_last':1,'update_high_low':1,'update_volume':1},)
    sql=aggregate_sql(unit,rules)
    assert "WHERE ticker='ABRpD'" in sql and "'ABRPD'" in sql
    mapped=MarketRequest(unit,'a'*64,'b'*64,rules,{},'c'*64,'d'*64,boundaries(day)['REGULAR'],True)
    ordinary=replace(mapped,unit=replace(unit,source_ticker=None))
    assert mapped.parent('bars')!=ordinary.parent('bars')
    assert ordinary.parent('bars')==replace(mapped,unit=replace(unit,source_ticker='ABRPD')).parent('bars')
    with pytest.raises(ValueError):require_exact_symbol(query,day,'ABRPD',source_ticker='OTHER')


def test_batched_discovery_freezes_unique_spellings_without_event_reads():
    from pipelines.certified_research.source import resolve_spellings
    from pipelines.certified_research.planning import MarketSession
    day=date(2026,1,2);sessions=(MarketSession(day,1,'ABRPD'),MarketSession(day,2,'TEST'))
    query=Mock(return_value=[dict(source_date=str(day),ticker='ABRpD',event_count=10)])
    guard=Mock();result=resolve_spellings(query,sessions,guard=guard)
    assert result['source_spellings']=={(1,day):'ABRpD'}
    assert result['sessions'][0].source_ticker=='ABRpD' and result['absent']==((2,day),)
    assert query.call_count==1 and 'events_2026' not in query.call_args.args[0]
    query.return_value.append(dict(source_date=str(day),ticker='ABRPD',event_count=10))
    with pytest.raises(ValueError,match='Ambiguous'):resolve_spellings(query,sessions,guard=guard)
