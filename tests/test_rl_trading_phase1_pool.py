"""Phase 1 keeps a bounded read-only connection pool across listing work."""
from datetime import date
from io import StringIO
from types import SimpleNamespace
from threading import get_ident
from time import sleep

from rich.console import Console

from research.rl_trading.v1 import build_phase1


def test_phase1_reuses_one_reader_per_worker_thread(tmp_path, monkeypatch):
    created = []

    class Reader:
        def __init__(self):
            self.owner = get_ident()
            self.closed = False
            created.append(self)

        def close(self):
            assert not self.closed
            self.closed = True

    def work(listing, day, source, plan, root, client):
        assert client.owner == get_ident()
        sleep(.005)
        return dict(ticker=listing['ticker'],status='completed',rows=57601,coverage={})

    monkeypatch.setattr(build_phase1.source_api,'reader',lambda threads: Reader())
    monkeypatch.setattr(build_phase1,'listing_work',work)
    day = date(2026,7,30)
    listings = [dict(ticker=f'T{i:02}',listing_id=f'I{i:02}') for i in range(30)]
    source = dict(build_id='build',definition_hash='definition',units={str(day):{}})
    args = SimpleNamespace(tickers=None,lookback_seconds=2,query_threads=1,workers=2)
    _,complete = build_phase1.phase1(day,source,listings,{},tmp_path,args,
        Console(file=StringIO(),force_terminal=False),{})
    assert complete
    assert 1 <= len(created) <= 2
    assert all(client.closed for client in created)
