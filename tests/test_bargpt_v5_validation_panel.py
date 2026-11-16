from io import StringIO
import json
import time

import pytest
from rich.console import Console

from research.bar_gpt.phase1_v5.panel import ValidationRate,read,render,duration


def progress(done,**extra):
    return dict(state='validating',stage='validation',mode='train',epoch=2,epochs=50,
        step=45580,validation_done=done,validation_total=10000,updated=1000.,
        _producer_identity=(42,123.),**extra)


def test_observed_rate_eta_and_duplicate_polling():
    observer=ValidationRate()
    assert observer.update(progress(0),now=0,wall=1000)['validation_eta_seconds'] is None
    observer.update(progress(1000),now=5,wall=1000)
    result=observer.update(progress(2000),now=10,wall=1000)
    assert result['validation_samples_per_second']==200
    assert result['validation_eta_seconds']==40
    result=observer.update(progress(2000),now=12,wall=1000)
    assert result['validation_samples_per_second']==pytest.approx(2000/12)


@pytest.mark.parametrize('change',[{'epoch':3},{'step':45590},{'_producer_identity':(99,456.)},
                                  {'validation_total':20000},{'validation_done':1}])
def test_restart_or_new_pass_discards_old_estimate(change):
    observer=ValidationRate();observer.update(progress(100),now=0,wall=1000)
    observer.update(progress(2100),now=10,wall=1000)
    snapshot=progress(2200);snapshot.update(change)
    assert observer.update(snapshot,now=12,wall=1000)['validation_samples_per_second'] is None


def test_stale_and_stalled_suppress_eta_then_remeasure():
    observer=ValidationRate();observer.update(progress(0),now=0,wall=1000)
    observer.update(progress(2000),now=10,wall=1000)
    stale=observer.update(progress(2100),now=11,wall=1031)
    assert stale['validation_rate_status']=='stale' and stale['validation_eta_seconds'] is None
    observer.update(progress(2200),now=12,wall=1000)
    stalled=observer.update(progress(2200),now=43,wall=1000)
    assert stalled['validation_rate_status']=='stalled' and stalled['validation_eta_seconds'] is None
    assert observer.update(progress(2300),now=44,wall=1000)['validation_samples_per_second']>0


def test_bounded_window_unknown_total_complete_and_phase_change():
    observer=ValidationRate()
    for t in range(121):result=observer.update(progress(t*10),now=t,wall=1000)
    assert result['validation_rate_window_seconds']<=61 and len(observer.points)<=62
    assert result['validation_samples_per_second']==10
    snapshot=progress(2000);snapshot['validation_total']=None
    observer.update(snapshot,now=122,wall=1000)
    snapshot['validation_done']=2100
    result=observer.update(snapshot,now=132,wall=1000)
    assert result['validation_samples_per_second']==10 and result['validation_eta_seconds'] is None
    snapshot=progress(10000)
    assert observer.update(snapshot,now=133,wall=1000)['validation_eta_seconds']==0
    snapshot.update(stage='training',state='running')
    result=observer.update(snapshot,now=134,wall=1000)
    assert result['validation_samples_per_second'] is None and not observer.points


def test_reader_is_read_only_and_binds_producer(tmp_path):
    snapshot=progress(100);snapshot.pop('_producer_identity')
    path=tmp_path/'progress.json';path.write_text(json.dumps(snapshot))
    (tmp_path/'process.json').write_text(json.dumps({'pid':42,'created':123.}))
    before=path.read_bytes();result=read(tmp_path)
    assert result['_producer_identity']==(42,123.) and path.read_bytes()==before


def test_reader_restores_last_completed_validation_after_resume(tmp_path):
    path=tmp_path/'progress.json';path.write_text(json.dumps(progress(0)))
    results=tmp_path/'results.json'
    results.write_text(json.dumps([{'epoch':1,'validation':{'masked_objective':.2}}]))
    restored=read(tmp_path)
    assert restored['validation']['masked_objective']==.2 and restored['completed_validation_epoch']==1
    results.write_text(json.dumps([{'epoch':2,'validation':{'masked_objective':.15}}]))
    restored=read(tmp_path)
    assert restored['validation']['masked_objective']==.15 and restored['completed_validation_epoch']==2
    snapshot=progress(0);snapshot['validation']={'masked_objective':.1};path.write_text(json.dumps(snapshot))
    assert read(tmp_path)['validation']['masked_objective']==.1


@pytest.mark.parametrize('width,height',[(120,38),(80,24),(60,16)])
@pytest.mark.parametrize('condition',['active','measuring','stale'])
def test_validation_speed_eta_fit_and_never_reuse_training_rate(width,height,condition):
    p=progress(2000);p.update(updated=time.time(),samples_per_second=999999,
        validation_samples_per_second=200.,validation_eta_seconds=40.,validation_rate_status=condition)
    if condition=='measuring':p.update(validation_samples_per_second=None,validation_eta_seconds=None)
    if condition=='stale':p['updated']-=31
    output=StringIO();Console(file=output,width=width,height=height,color_system=None).print(render(p,width,height))
    text=output.getvalue();assert 'ETA' in text and '999,999' not in text
    if condition=='active':assert '0m 40s' in text
    else:assert '0m 40s' not in text
    assert len(text.splitlines())<=height and all(len(line)<=width for line in text.splitlines())
    assert '\x1b[' not in text


def test_duration_unknown_and_long_eta():
    assert duration(None)=='—' and duration(3601)=='1h 00m' and duration(90000)=='1d 01h'
