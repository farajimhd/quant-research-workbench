import pytest
from src.market_engine import v7_catalog as V
from src.market_engine.level_book_store import write,read
from src.market_engine.historical_level_checkpoint import digest
from research.level_book.v7.campaign_source import source_hash
from tests.test_v7_qmd import Catalog as Fixture


def prepared(root,monkeypatch):
    monkeypatch.setattr(V,'CAMPAIGNS',('main',))
    plan=dict(extraction_version=V.EXTRACTION_VERSION,band_config=V.CONFIG,
        start='2026-08-20',end='2026-08-21',rules=[],
        rows=[dict(ticker='TEST',status='eligible',directory='TEST')])
    plan['plan_hash']=digest(plan);write(root/'main'/'plan.json',plan)
    target=root/'main'/'tickers'/'TEST'
    days=[dict(ticker='TEST',source_date=day) for day in ('2026-08-20','2026-08-21')]
    write(target/'source-plan.json',dict(plan_hash=plan['plan_hash'],days=days))
    book=Fixture(root).prior
    write(target/'books'/'2026-08-20.json.gz',book)
    write(target/'receipts'/'2026-08-20.json',dict(state='complete',source_hash=source_hash(days[0],[]),
        parent_hash=book['prior_checkpoint_hash'],checkpoint_hash=book['checkpoint_hash']))
    return target,days


def test_exact_preceding_session_never_silently_uses_older_book(tmp_path,monkeypatch):
    prepared(tmp_path,monkeypatch);catalog=V.Catalog(tmp_path)
    assert catalog.select('TEST','2026-08-21')[0]['session']=='2026-08-20'
    with pytest.raises(ValueError,match='no stale or legacy fallback'):catalog.select('TEST','2026-08-22')
    with pytest.raises(ValueError,match='No V7 historical coverage'):catalog.select('OTHER','2026-08-21')


def test_verified_empty_receipt_allows_previous_nonempty_book(tmp_path,monkeypatch):
    target,days=prepared(tmp_path,monkeypatch)
    write(target/'receipts'/'2026-08-21.json',dict(state='empty',source_hash=source_hash(days[1],[])))
    book,provenance=V.Catalog(tmp_path).select('TEST','2026-08-22')
    assert book['session']=='2026-08-20'
    assert provenance['verified_empty_sessions']==['2026-08-21']


def test_corrupt_checkpoint_and_disordered_source_fail_closed(tmp_path,monkeypatch):
    target,_=prepared(tmp_path,monkeypatch)
    path=target/'books'/'2026-08-20.json.gz';book=read(path);book['ticker']='OTHER';write(path,book,immutable=False)
    with pytest.raises(ValueError,match='hash mismatch'):V.Catalog(tmp_path).select('TEST','2026-08-21')
    path=target/'source-plan.json';source=read(path);source['days'].reverse();write(path,source,immutable=False)
    with pytest.raises(ValueError,match='unique, ordered'):V.Catalog(tmp_path).select('TEST','2026-08-21')
