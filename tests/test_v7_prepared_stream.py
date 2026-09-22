import json
import sqlite3
from copy import deepcopy

import pytest

from src.market_engine.v7_prepared_stream import PreparedStream
from src.market_engine.v7_snapshot_transport import Decoder
from tests.test_v7_qmd import make, at


def fixture(path, bars, *, contract=True, authority_name='qmd_history_prepared_closed_bars', clock='sip'):
    db=sqlite3.connect(path)
    db.executescript('CREATE TABLE strategy_frame_streams(ticker,timeframe,authority_json); CREATE TABLE strategy_frames(ticker,timeframe,as_of_us,sequence,bar_json);')
    token = ('1:20:0:updated:Archive:1:2:split-sha256:abc:' + (
        'structure-input-v1:archive-sip-condition:recent-participant-aware:abc'
        if clock == 'sip' else 'execution-clock-v1:1:20:0:1:updated'))
    authority=dict(authority=authority_name,calculation_revision='qmd-derived-v59-0405-et',
        complete_for_history=True,source_plan_hash='frozen-plan',
        revision_token=token if contract else 'uncertified-source')
    db.execute('INSERT INTO strategy_frame_streams VALUES (?,?,?)',('TEST','1s',json.dumps(authority)))
    db.executemany('INSERT INTO strategy_frames VALUES (?,?,?,?,?)',[
        ('TEST','1s',int(b['t']*1e6),i,json.dumps(dict(sym='TEST',timeframe='1s',bar_end=at(b['t']).isoformat(),**{k:v for k,v in b.items() if k!='t'})))
        for i,b in enumerate(bars)])
    db.commit();db.close()


def model(value):
    return {k:v for k,v in value.items() if k!='source_audit'}


@pytest.mark.parametrize('revision', ['qmd-derived-v58', 'qmd-derived-v59-0405-et'])
def test_exact_every_prefix_batch_rewind_and_retained_state(tmp_path, revision):
    service,source=make(tmp_path)
    path=tmp_path/'bars.sqlite3';fixture(path,source.bars)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE strategy_frame_streams SET authority_json=json_set(authority_json,'$.calculation_revision',?)", (revision,))
    stream=PreparedStream(service,path,'2026-08-21','fixture')
    try:
        receipt=stream.prepare(['TEST'])
        assert stream.states['TEST']['engine'].bars_processed==0
        assert not source.calls
        decoder=Decoder();saved=[]
        for b in source.bars:
            cutoff=at(b['t'])
            packet=stream.snapshot('TEST',cutoff,decoder.version)
            actual=deepcopy(decoder.decode(json.loads(json.dumps(packet))))
            expected=service.snapshot('TEST',cutoff,include_segments=False,cursor_id='reference')
            assert model(actual)==model(expected)
            saved.append(actual)
        original=deepcopy(stream.states['TEST']['engine'].checkpoint())
        assert receipt==stream.prepare(['TEST'])
        for index in (2,9,4,25):
            actual=decoder.decode(stream.snapshot('TEST',at(source.bars[index]['t']),decoder.version))
            assert actual==saved[index]
        assert stream.states['TEST']['engine'].checkpoint()==original
        second=PreparedStream(service,path,'2026-08-21','fixture')
        try:
            second.prepare(['TEST'])
            end=Decoder().decode(second.snapshot('TEST',at(source.bars[-1]['t'])))
            assert end==saved[-1]
            assert second.states['TEST']['engine'].checkpoint()==original
        finally:second.close()
    finally:stream.close();service.close()


def test_future_tail_does_not_change_prefix_and_capacity_fails(tmp_path):
    service,source=make(tmp_path)
    full=tmp_path/'full.sqlite3';short=tmp_path/'short.sqlite3'
    fixture(full,source.bars);fixture(short,source.bars[:10])
    a=PreparedStream(service,full,'2026-08-21','fixture');b=PreparedStream(service,short,'2026-08-21','fixture')
    try:
        a.prepare(['TEST']);b.prepare(['TEST'])
        cutoff=at(source.bars[9]['t'])
        assert model(Decoder().decode(a.snapshot('TEST',cutoff)))==model(Decoder().decode(b.snapshot('TEST',cutoff)))
        assert a.states['TEST']['engine'].checkpoint()==b.states['TEST']['engine'].checkpoint()
        limited=PreparedStream(service,full,'2026-08-21','fixture',max_bytes=1)
        try:
            with pytest.raises(ValueError,match='memory budget'):limited.prepare(['TEST'])
            assert not limited.states
        finally:limited.close()
    finally:a.close();b.close();service.close()


def test_incompatible_clock_is_rejected(tmp_path):
    service,source=make(tmp_path);path=tmp_path/'bars.sqlite3';fixture(path,source.bars,contract=False)
    stream=PreparedStream(service,path,'2026-08-21','fixture')
    try:
        with pytest.raises(ValueError,match='source-clock'):stream.prepare(['TEST'])
    finally:stream.close();service.close()


def test_scalar_bundle_execution_clock_is_certified(tmp_path):
    service, source = make(tmp_path)
    path = tmp_path / 'scalar.sqlite3'
    fixture(path, source.bars, authority_name='qmd_history_derived_bundle', clock='execution')
    stream = PreparedStream(service, path, '2026-08-21', 'fixture')
    try:
        receipt = stream.prepare(['TEST'])
        assert receipt[0]['bars'] == len(source.bars)
        assert stream.states['TEST']['engine'].bars_processed == 0
        assert not source.calls
    finally:
        stream.close()
        service.close()


@pytest.mark.parametrize('field,value', [
    ('source_plan_hash', ''),
    ('complete_for_history', False),
    ('calculation_revision', 'unknown-revision'),
    ('authority', 'unverified-bundle'),
])
def test_scalar_bundle_missing_authority_still_fails_closed(tmp_path, field, value):
    service, source = make(tmp_path)
    path = tmp_path / 'uncertified.sqlite3'
    fixture(path, source.bars, authority_name='qmd_history_derived_bundle', clock='execution')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE strategy_frame_streams SET authority_json=json_set(authority_json, ?, ?)',
                   (f'$.{field}', value))
    stream = PreparedStream(service, path, '2026-08-21', 'fixture')
    try:
        with pytest.raises(ValueError, match='source-clock'):
            stream.prepare(['TEST'])
        assert not stream.states
    finally:
        stream.close()
        service.close()


@pytest.mark.parametrize('changed', [False, True])
def test_resume_window_difference_requires_exact_canonical_bars(tmp_path, monkeypatch, changed):
    service,source=make(tmp_path)
    path=tmp_path/'resume.sqlite3';fixture(path,source.bars)
    expected={'token':'old-session-split-window'}
    canonical=deepcopy(source.bars)
    if changed:canonical[0]['volume']+=1
    monkeypatch.setattr(source,'seconds',lambda *args:(canonical,{'source_revision':expected}))
    stream=PreparedStream(service,path,'2026-08-21','fixture')
    try:
        if changed:
            with pytest.raises(ValueError,match='bars differ'):stream.prepare(['TEST'],{'TEST':expected})
            assert not stream.states
        else:
            stream.prepare(['TEST'],{'TEST':expected})
            assert stream.states['TEST']['engine'].bars_processed==0
            snapshot=Decoder().decode(stream.snapshot('TEST',stream.begin))
            assert snapshot['source_audit']['source_revision']['verified_resume_revision']==expected
    finally:stream.close();service.close()


def test_prepared_seed_cannot_be_from_the_future(tmp_path):
    service,source=make(tmp_path)
    path=tmp_path/'future.sqlite3';fixture(path,source.bars)
    stream=PreparedStream(service,path,'2026-08-21','fixture')
    service.catalog.prior['available_at']=stream.end.timestamp()+1
    try:
        with pytest.raises(ValueError,match='not available'):stream.prepare(['TEST'])
        assert not stream.states
    finally:stream.close();service.close()


def test_spilled_state_preserves_every_prefix_delta_and_retry(tmp_path):
    service, source = make(tmp_path)
    path = tmp_path / 'spill.sqlite3'
    fixture(path, source.bars)
    stream = PreparedStream(service, path, '2026-08-21', 'fixture')
    stream.states.capacity = 1
    directory = stream.states.directory.name
    try:
        stream.prepare(['TEST'])
        decoder = Decoder()
        saved = []
        for bar in source.bars:
            # Force the live engine, arrays, aliases and encoder base through
            # disk between every pair of observations, like interleaved tickers.
            stream.states['other'] = {'sentinel': True}
            assert len(stream.states.resident) == 1
            packet = stream.snapshot('TEST', at(bar['t']), decoder.version)
            actual = deepcopy(decoder.decode(json.loads(json.dumps(packet))))
            expected = service.snapshot('TEST', at(bar['t']), include_segments=False, cursor_id='reference')
            assert model(actual) == model(expected)
            saved.append(actual)
        stream.states['other'] = {'sentinel': True}
        assert decoder.decode(stream.snapshot('TEST', at(source.bars[9]['t']), decoder.version)) == saved[9]
        assert stream.prepare(['TEST'])[0]['bars'] == len(source.bars)
    finally:
        stream.close()
        service.close()
    from pathlib import Path
    assert not Path(directory).exists()


def test_spill_write_failure_keeps_resident_authority(tmp_path, monkeypatch):
    from src.market_engine.v7_resident_states import ResidentStates
    import pickle
    states = ResidentStates(tmp_path, capacity=1)
    original = {'mutable': [1, 2, 3]}
    states['first'] = original
    def fail(*args, **kwargs):
        raise OSError('disk full')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(pickle, 'dump', fail)
            with pytest.raises(OSError, match='disk full'):
                states['second'] = {'mutable': []}
        assert states['first'] is original
        assert 'second' not in states
        states['second'] = {'mutable': []}
        assert states['first'] == original
    finally:
        states.clear()
