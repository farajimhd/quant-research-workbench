"""Truthful full-pass units, compact layout, read-only lifecycle and plain output."""
import io
import json
from pathlib import Path
import pytest
from rich.console import Console
from research.bar_gpt.panels import phase2_training_v1 as panel


def fixture(root):
    root.mkdir(exist_ok=True)
    p=dict(state='running',stage='training',updated=1000.,epoch=1,data_pass=1,epochs=10,
        epoch_kind='full dataset pass',step=1061000,pass_fraction=.034,
        total_training_blocks=12188930,interval_origins=11264000,done=11264000,
        validation_every_updates=150000,learning_rate=.0002993,samples_per_second=27000,
        gpu_util=90,gpu_memory_mib=24000)
    history=[dict(epoch=i,data_pass=1,step=i*150000,validation=dict(origins=708600,
        mse=.04-i*.001,cosine=.95,persistence_mse=.067,training_mean_mse=.042)) for i in range(1,8)]
    for name,value in [('progress.json',p),('results.json',history),
            ('checkpoint_last.proof.json',{'step':1061000}),
            ('wandb-identity.json',{'name':'phase2-latent-v1-20261114'})]:
        (root/name).write_text(json.dumps(value))
    (root/'metrics.jsonl').write_text(json.dumps(dict(step=1061000,train_loss=.035,gradient_norm=.02))+'\n')
    return p


def snapshot(root):
    observer=panel.Observer(root);s=observer.read(now=1002)
    s['resources']={'ram':400,'disk':638};return s


def text(s,width=110,height=32):
    output=io.StringIO();c=Console(file=output,width=width,height=height,no_color=True)
    c.print(panel.render(s,width,height));return output.getvalue()


def test_legacy_validation_epochs_never_inflate_full_pass_progress(tmp_path):
    p=fixture(tmp_path);s=snapshot(tmp_path);t=text(s)
    assert '0/10 complete' in t and 'Pass 1' in t and 'validations 7' in t
    assert s['previous']==1050000 and s['next_step']==1200000
    assert '11,264,000 origins since validation' in t
    p.pop('epoch_kind');(tmp_path/'progress.json').write_text(json.dumps(p))
    t=text(snapshot(tmp_path));assert 'legacy labels' in t and '0/10 complete' not in t


def test_validation_progress_uses_actual_population_and_unknown_is_not_a_percentage(tmp_path):
    p=fixture(tmp_path);p.update(stage='validation',step=1200000,done=354300)
    (tmp_path/'progress.json').write_text(json.dumps(p));s=snapshot(tmp_path)
    assert '354,300/708,600 origins' in text(s)
    (tmp_path/'results.json').write_text('[]');s=snapshot(tmp_path)
    assert s['validation_total'] is None and 'total unknown' in text(s)


@pytest.mark.parametrize('width,height',[(120,32),(80,24),(56,14),(40,12)])
def test_layout_fits_real_terminal_dimensions(width,height,tmp_path):
    fixture(tmp_path);s=snapshot(tmp_path);output=text(s,width,height)
    assert max(map(len,output.splitlines()))<=width
    assert len(output.splitlines())<=height
    assert 'Step' in output and 'Checkpoint' in output


def test_stale_and_read_errors_keep_last_trustworthy_snapshot(tmp_path):
    fixture(tmp_path);observer=panel.Observer(tmp_path);a=observer.read(now=1002)
    (tmp_path/'progress.json').write_text('{broken')
    b=observer.read(now=1100)
    assert a['progress']==b['progress'] and b['warnings']
    t=text(b);assert 'STALE' in t or 'Status unavailable' in t


@pytest.mark.parametrize('state',['complete','stopped','failed','learning_guard_stop'])
def test_terminal_outcome_stays_visible_and_is_not_a_stale_running_job(state,tmp_path):
    p=fixture(tmp_path);p['state']=state
    if state=='failed':p['error']='Source payload hash mismatch; inspect progress.json'
    (tmp_path/'progress.json').write_text(json.dumps(p));s=panel.Observer(tmp_path).read(now=5000)
    assert not any('STALE' in x for x in s['warnings'])
    assert state.upper() in text(s)
    if state=='complete':assert '10/10 complete' in text(s)


def test_recent_rate_resets_at_stage_change_and_counter_rewind(tmp_path):
    p=fixture(tmp_path);o=panel.Observer(tmp_path);o.read(now=1000)
    p.update(step=1061300,interval_origins=p['interval_origins']+307200,updated=1012)
    (tmp_path/'progress.json').write_text(json.dumps(p));s=o.read(now=1012)
    # Resource warnings may suppress live rate on a low-space test laptop.
    assert len(o.samples)==2
    p.update(stage='validation',done=1024,updated=1014)
    (tmp_path/'progress.json').write_text(json.dumps(p));assert o.read(now=1014)['rate'] is None
    assert len(o.samples)==1


def test_cumulative_origins_are_not_doubled_when_validation_result_arrives(tmp_path):
    p=fixture(tmp_path);history=json.loads((tmp_path/'results.json').read_text())
    for h in history:h['train']={'origins':153600000}
    (tmp_path/'results.json').write_text(json.dumps(history))
    assert snapshot(tmp_path)['trained_origins']==7*153600000+p['interval_origins']
    p.update(step=1050000,stage='validation',done=708600)
    (tmp_path/'progress.json').write_text(json.dumps(p))
    assert snapshot(tmp_path)['trained_origins']==7*153600000


def test_missing_snapshot_and_empty_history_render_without_false_completion(tmp_path):
    output=text(snapshot(tmp_path))
    assert 'no trustworthy snapshot yet' in output
    assert '0/10 complete' not in output


def test_redirected_real_entrypoint_has_no_ansi_or_source_writes(tmp_path,capsys,monkeypatch):
    fixture(tmp_path);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setenv('NO_COLOR','1')
    assert panel.main(['--run',str(tmp_path),'--once','--width','80','--height','24'])==0
    output=capsys.readouterr().out
    assert '\x1b' not in output and 'BarGPT' in output
    assert '█' in output and '░' in output and '-----' not in output
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert panel.main(['--run',str(tmp_path),'--json'])==0
    assert json.loads(capsys.readouterr().out)['progress']['data_pass']==1


def test_ctrl_c_closes_only_observer_and_restores_terminal(tmp_path,monkeypatch):
    fixture(tmp_path);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    output=io.StringIO();console=Console(file=output,width=80,height=24,force_terminal=True)
    monkeypatch.setattr(panel,'Console',lambda **kw:console)
    def interrupt(seconds):raise KeyboardInterrupt
    monkeypatch.setattr(panel.time,'sleep',interrupt)
    assert panel.main(['--run',str(tmp_path)])==0
    assert 'Panel closed; training continues.' in output.getvalue()
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
