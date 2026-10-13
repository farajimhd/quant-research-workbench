"""Bootstrap needs native absence reads; late publications revoke that proof."""
import asyncio
from dataclasses import replace
import json
from threading import get_ident

import pytest

from pipelines.market_sip.events.market_day_predecessor_absence import (
    certify_empty_market_day_predecessors, reverify_empty_market_day_predecessors,
)
from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger
from test_market_day_predecessor import supplied, TARGET
from test_arte_market_day_certification import DAY
from test_arte_market_day_publisher import FakeClickHouse


@pytest.mark.parametrize('published', [False, True])
def test_empty_proof_is_owned_readonly_and_cannot_hide_published_parent(published):
    current, definition, _ = supplied()
    main = get_ident()
    readers = []
    class Reader(FakeClickHouse):
        def __init__(self): super().__init__(); self.closed = False; self.threads = []; readers.append(self)
        def execute(self, query):
            self.threads.append(get_ident())
            if query == "SELECT getSetting('readonly')": return '1'
            if 'INNER JOIN' in query:
                assert 'LIMIT 2' in query and 'calculation_source_hash' in query
                return json.dumps(dict(session_date=DAY, ticker='TEST')) if published else ''
            return super().execute(query)
        def close(self): self.closed = True
    profile = (definition['calculation_source'], definition['rules_hash'], definition['version'])
    work = certify_empty_market_day_predecessors(Reader, current.build_id, ((DAY, 'TEST'),), profile=profile)
    if published:
        with pytest.raises(ValueError, match='compatible published'):
            asyncio.run(work)
    else:
        proof = asyncio.run(work)
        registry = MarketDayPredecessorLedger(current, definition, (), empty_predecessors=proof)
        assert registry.for_target(TARGET, 'TEST').candidates(DAY, 'TEST', current.build_id, 'arte', *profile) == []
        with pytest.raises(ValueError, match='unchanged native'):
            replace(proof, scopes=(('2026-08-17', 'TEST'),)).verify()
    assert all(reader.closed and all(thread != main for thread in reader.threads) for reader in readers)


def test_late_compatible_receipt_blocks_absence_revalidation():
    current, definition, _ = supplied()
    published = False
    class Reader(FakeClickHouse):
        def execute(self, query):
            if query == "SELECT getSetting('readonly')": return '1'
            if 'INNER JOIN' in query: return json.dumps(dict(session_date=DAY, ticker='TEST')) if published else ''
            return super().execute(query)
        def close(self): pass
    profile = (definition['calculation_source'], definition['rules_hash'], definition['version'])
    proof = asyncio.run(certify_empty_market_day_predecessors(Reader, current.build_id, ((DAY, 'TEST'),), profile=profile))
    published = True
    with pytest.raises(ValueError, match='compatible published'):
        asyncio.run(reverify_empty_market_day_predecessors(proof, Reader))
