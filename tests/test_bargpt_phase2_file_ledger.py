"""Actual file journal recovery, legacy parity and no-SQL generation boundary."""

import json
from pathlib import Path
import sqlite3

import polars as pl
import pytest

from research.bar_gpt.v5.phase2_ar_shards.ledger import Catalog, FileLedger, TABLES, STRINGS
from research.bar_gpt.v5.phase2_ar_shards.migrate_ledger import migrate
from research.bar_gpt.v5.shards.contract import digest


@pytest.fixture(autouse=True)
def fixture_memory_reserve(monkeypatch):
    from types import SimpleNamespace
    from research.bar_gpt.v5.phase2_ar_shards import migrate_ledger
    monkeypatch.setattr(migrate_ledger.psutil,'virtual_memory',lambda:SimpleNamespace(available=200*1024**3))


def receipt(ticker, liquid, blocks):
    return dict(ticker=ticker,month='2025-01',hash=ticker,bytes=100,
                liquid=liquid,splits=[0]*blocks,blocks=blocks,
                stats=dict(source_origins=blocks*4096+1,warmup_deferred=0,
                           boundary_tail_deferred=0,pending_origins=1))


def test_generation_recovery_never_opens_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:pytest.fail('Generation opened SQLite'))
    c=Catalog(tmp_path)
    c.register(tmp_path/'A',receipt('A',1,3))
    c.flush(True)
    c.register(tmp_path/'B',receipt('B',0,1))
    expected=c.stats();selection=c.export('binding');c.close()
    r=Catalog(tmp_path)
    assert r.stats()==expected and r.export('binding')==selection
    assert sorted(row[4] for row in r.blocks)==list(range(4))
    r.close()
    assert not (tmp_path/'catalog.sqlite').exists()


def test_journal_tampering_is_rejected_before_replay(tmp_path):
    c=Catalog(tmp_path);c.register(tmp_path/'A',receipt('A',1,3));c.close()
    part=next((tmp_path/'ledger-file-v1').glob('journal-*.parquet'))
    part.write_bytes(part.read_bytes()+b'mutation')
    with pytest.raises(ValueError,match='part changed'):Catalog(tmp_path)


def test_uncommitted_orphan_is_not_a_transaction(tmp_path):
    c=Catalog(tmp_path);c.register(tmp_path/'A',receipt('A',1,3));c.close()
    root=tmp_path/'ledger-file-v1'
    (root/'journal-orphan.parquet').write_bytes(b'incomplete unpublished part')
    r=Catalog(tmp_path);assert r.stats()['units']==1;r.close()


def test_legacy_requires_explicit_migration(tmp_path):
    (tmp_path/'catalog.sqlite').write_bytes(b'legacy evidence')
    with pytest.raises(ValueError,match='migration required'):Catalog(tmp_path)


def legacy_fixture(root, corrupt=None):
    plan=dict(version='fixture');plan['hash']=digest(plan)
    (root/'plan.json').write_text(json.dumps(plan));(root/'STOP').write_text('USER_TEST_MIGRATION')
    binding=plan['hash'];c=sqlite3.connect(root/'catalog.sqlite')
    for name,fields in TABLES.items():
        if name=='completed_months':fields=fields[:-1]
        c.execute('CREATE TABLE '+name+'('+','.join(k+(' TEXT' if k in STRINGS else ' INTEGER') for k in fields)+')')
    paths={t:str(Path('shards')/digest(t)[:24]/'2025-01-02') for t in ('A','B')}
    for ticker,liquid,blocks,start in [('A',1,3,0),('B',0,1,3)]:
        c.execute('INSERT INTO units VALUES(?,?,?,?,?,?,?)',(paths[ticker],ticker,100,0,blocks*4096+1,0,0))
        c.execute('INSERT INTO tails VALUES(?,?,?)',(ticker,'2025-01-02',1))
        c.executemany('INSERT INTO blocks VALUES(?,?,?,?,?)',[(paths[ticker],i,0,liquid,start+i) for i in range(blocks)])
    unit=dict(ticker='C',month='2025-01-02')
    deferred=dict(binding=binding,unit=unit,stats=dict(source_origins=17))
    seal=digest(deferred)
    if corrupt=='report':deferred['stats']['source_origins']=18
    key=digest(['C','2025-01-02']) if corrupt!='key' else 'foreign'
    c.execute('INSERT INTO deferrals VALUES(?,?,?)',(key,json.dumps(deferred),seal))
    complete=dict(binding=binding,unit=dict(ticker='A',month='2025-01'),sessions=[dict(session='2025-01-02',kind='published',seal='A')])
    c.execute('INSERT INTO completed_months VALUES(?,?,?)',(digest(['A','2025-01']),json.dumps(complete),digest(complete)))
    c.commit();c.close()
    return binding,paths


def test_read_only_legacy_migration_preserves_all_fields_and_selection(tmp_path,monkeypatch):
    binding,paths=legacy_fixture(tmp_path)
    before=(tmp_path/'catalog.sqlite').read_bytes()
    report=migrate(tmp_path)
    assert report['passed'] and report['exact_tables'] and report['selected_order_exact']
    assert report['tables']['deferrals']['rows']==1 and report['stats']['selected_origins']==16384
    assert before==(tmp_path/'catalog.sqlite').read_bytes()
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:pytest.fail('New ledger accessed legacy SQLite'))
    r=Catalog(tmp_path)
    assert r.deferred_sessions==1 and r.deferred_source_origins==17
    assert r.is_complete(dict(ticker='A',month='2025-01'))
    with pytest.raises(ValueError,match='unit changed'):r.is_complete(dict(ticker='A',month='2025-01',foreign=True))
    assert r.blocks==[[paths['A'],i,0,1,i] for i in range(3)]+[[paths['B'],0,0,0,3]]
    r.close()


@pytest.mark.parametrize('corrupt',['report','key'])
def test_migration_rejects_corrupt_legacy_seals(tmp_path,corrupt):
    legacy_fixture(tmp_path,corrupt)
    with pytest.raises(ValueError,match='hash/key/binding'):migrate(tmp_path)
    assert not (tmp_path/'ledger-file-v1/HEAD.json').exists()


def test_migration_checkpoint_mutation_is_rejected(tmp_path):
    legacy_fixture(tmp_path);migrate(tmp_path)
    part=next((tmp_path/'ledger-file-v1').glob('checkpoint-deferrals-*.parquet'))
    part.write_bytes(part.read_bytes()+b'mutation')
    with pytest.raises(ValueError,match='part changed'):Catalog(tmp_path)


def test_manifest_segment_rollover_preserves_event_order(tmp_path):
    ledger=FileLedger(tmp_path)
    for i in range(130):
        ledger.append('test',str(i),dict(i=i));ledger.flush(True)
    assert len(ledger.manifest['segments'])==1 and len(ledger.manifest['parts'])==2
    ledger.close()
    again=FileLedger(tmp_path)
    assert [body['i'] for _,_,body in again.events()]==list(range(130))


def test_immutable_manifest_collision_preserves_incident_bytes(tmp_path):
    from research.bar_gpt.v5.phase2_ar_shards.ledger import sealed_write
    path=tmp_path/'manifest-existing.json'
    sealed_write(path,dict(value=1))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='manifest collision'):
        sealed_write(path,dict(value=2))
    assert path.read_bytes()==before


def test_journal_preserves_disk_reserve(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from research.bar_gpt.v5.phase2_ar_shards import ledger
    c=Catalog(tmp_path);c.register(tmp_path/'A',receipt('A',1,3))
    monkeypatch.setattr(ledger.psutil,'disk_usage',lambda _:SimpleNamespace(free=149*1024**3))
    with pytest.raises(OSError,match='disk reserve'):c.flush(True)
    assert not list((tmp_path/'ledger-file-v1').glob('journal-*.parquet'))


def test_missing_head_never_resets_existing_ledger(tmp_path):
    c=Catalog(tmp_path);c.register(tmp_path/'A',receipt('A',1,3));c.close()
    head=tmp_path/'ledger-file-v1/HEAD.json';head.unlink()
    with pytest.raises(ValueError,match='HEAD is missing'):Catalog(tmp_path)
    assert not head.exists()
    assert list((tmp_path/'ledger-file-v1').glob('journal-*.parquet'))
