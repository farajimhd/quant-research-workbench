from datetime import date
from unittest.mock import Mock

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
