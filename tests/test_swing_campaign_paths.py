import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import build_swing_book_campaign as C
from scripts.swing_book_paths import ticker_directory
from scripts import swing_reader_upgrade as U


@pytest.mark.parametrize('ticker',['CON','PRN','AUX','NUL','COM1','LPT9','CON.A','ABC.'])
def test_windows_safe_name_is_writable_and_collision_free(tmp_path,ticker):
    name=ticker_directory(ticker)
    assert name.startswith('_ticker_') and not PureWindowsPath(name).is_reserved()
    folder=tmp_path/name;folder.mkdir()
    (folder/'progress.json').write_text('{}')
    assert (folder/'progress.json').read_text()=='{}'
    assert name==ticker_directory(ticker,lowercase=True)


def test_safe_existing_paths_and_ticker_identity_unchanged():
    assert ticker_directory('SUGP')=='SUGP'
    assert ticker_directory('BRK.B',lowercase=True)=='brk.b'
    with pytest.raises(ValueError):ticker_directory('../CON')


def legacy_manifest(root):
    hashes={p:sha256((C.ROOT/p).read_bytes()).hexdigest() for p in C.TRACKED}
    old=next(U.path_baselines(hashes))
    rows=[dict(ticker=t,status=s,days=1,report=str(root.parent/(root.name+'-v6')/t.lower()/'report.json'),
               progress_file=str(root/'workers'/t/'progress.json'))
          for t,s in [('CON','queued'),('SUGP','completed'),('JUNS','interrupted'),('OTHER','deferred')]]
    return dict(schema_version=2,book_version='causal-swing-closing-book-6',code_hash=C.P.digest(old),
        reader='indexed',workers=1,threads=2,universe=[],universe_hash=C.P.digest([]),rows=rows)


def test_explicit_migration_is_idempotent_and_preserves_other_rows(tmp_path):
    m=legacy_manifest(tmp_path);before=deepcopy(m)
    with pytest.raises(ValueError):C.reader_for_manifest(m)
    assert len(C.upgrade_paths(tmp_path,m))==2
    assert C.reader_for_manifest(m)=='indexed'
    assert m['rows'][1:]==before['rows'][1:]
    assert m['code_hash']==before['code_hash'] and m['universe_hash']==before['universe_hash']
    assert C.upgrade_paths(tmp_path,m)==[]
    assert '_ticker_434f4e' in m['rows'][0]['report']


def test_migration_rejects_different_engine(tmp_path):
    m=legacy_manifest(tmp_path)
    m['code_hash']='not-the-pinned-code'
    with pytest.raises(ValueError,match='Unsupported path upgrade'):C.upgrade_paths(tmp_path,m)


def test_builder_identity_keeps_old_checkpoint_and_rejects_source_change():
    hashes={p:'current' for p in ['scripts/build_swing_structure_book.py','scripts/swing_reader_upgrade.py',
        'scripts/swing_book_paths.py','src/market_engine/swing_book_v6.py','src/backend/swing_book_source.py']}
    old=next(U.path_baselines(hashes));previous={'code_hash':U.digest(old)}
    assert U.build_identity(hashes,previous,indexed=True)==previous['code_hash']
    hashes['src/backend/swing_book_source.py']='different'
    with pytest.raises(ValueError):U.build_identity(hashes,previous,indexed=True)


def test_controller_exception_is_durable_and_closes_spawn_log(tmp_path,monkeypatch):
    m=legacy_manifest(tmp_path)
    m.update(code_hash=C.code_hash(),reader='indexed')
    m['rows']=m['rows'][1:2]
    m['rows'][0]['status']='queued'
    C.save(tmp_path,m)
    streams=[]
    def fail(*args,**kw):
        streams.append(kw['stdout'])
        raise OSError('injected dispatch failure')
    monkeypatch.setattr(C.subprocess,'Popen',fail)
    with pytest.raises(OSError,match='injected dispatch failure'):
        C.run(SimpleNamespace(runtime=tmp_path,retry_failed=False,env_file=tmp_path/'unused',progress_seconds=1))
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert 'injected dispatch failure' in saved['stop_reason']
    event=json.loads((tmp_path/'controller-errors.jsonl').read_text())
    assert event['error_type']=='OSError' and 'Traceback' in event['traceback']
    assert streams[0].closed and saved['rows'][0]['status']=='queued'
