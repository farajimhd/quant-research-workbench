"""Validation-aligned cosine peaks, data continuity and boundary recovery."""
import copy
import random
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_v1.schedule import ValidationCosine
from research.bar_gpt.phase2_v1.train import train,load_checkpoint
from research.bar_gpt.phase2_v1.model import Forecaster
from research.bar_gpt.phase2_v1.config import Config
from research.bar_gpt.phase2_v1.run_train import arguments
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_cosine_peaks_floor_restart_and_one_time_warmup():
    s=ValidationCosine(3e-4,5e-5,10,warmup=2,gamma=.9)
    values=[]
    for _ in range(30):values.append(s.value());s.updates+=1
    assert values[0]==pytest.approx(1.75e-4)
    assert values[1]==pytest.approx(3e-4) and values[2]==pytest.approx(3e-4)
    assert values[9]==pytest.approx(5e-5)
    assert values[10]==pytest.approx(2.7e-4) and values[19]==pytest.approx(4.5e-5)
    assert values[20]==pytest.approx(2.43e-4) and values[29]==pytest.approx(4.05e-5)
    assert all(a>=b for a,b in zip(values[2:9],values[3:10]))
    state=copy.deepcopy(s.state_dict());other=ValidationCosine(3e-4,5e-5,10,2,.9)
    other.load_state_dict(state);assert other.value()==s.value()
    with pytest.raises(ValueError,match='policy changed'):
        ValidationCosine(3e-4,5e-5,11,2,.9).load_state_dict(state)


@pytest.mark.parametrize('period,warmup',[(0,0),(10,10),(10,-1)])
def test_invalid_schedule_fails_closed(period,warmup):
    with pytest.raises(ValueError):ValidationCosine(3e-4,5e-5,period,warmup)


def setup(tmp_path):
    units=[session(tmp_path/'train'),session(tmp_path/'val','2026-01-05')]
    def args(root,resume=None):
        a=options(root,resume);a.epochs=3;a.validation_every=2;a.warmup_updates=0;a.lr_restart_decay=.9
        a.batch_size=64;return a
    def fresh():
        random.seed(11);np.random.seed(11);torch.manual_seed(11)
        return Forecaster(Config(layers=1,feedforward=64)),Teacher()
    return units,args,fresh


def test_logical_cycles_validate_once_keep_cursor_and_wrap_only_at_data_end(tmp_path):
    units,args,fresh=setup(tmp_path);root=tmp_path/'run';root.mkdir();model,teacher=fresh()
    logged=[]
    class Record(Reporter):
        def log(self,values,step):logged.append((step,values))
    assert train(model,teacher,'pinned',units,{'test':'cycles'},args(root),Record())=='complete'
    value=load_checkpoint(root/'checkpoint_last.pt',{'test':'cycles'})
    assert value['step']==6 and value['epoch']==3 and len(value['history'])==3
    assert [h['step'] for h in value['history']]==[2,4,6]
    assert [h['train']['origins'] for h in value['history']]==[128,65,128]
    assert [h['epoch_start_learning_rate'] for h in value['history']]==pytest.approx([3e-4,2.7e-4,2.43e-4])
    assert [h['data_pass'] for h in value['history']]==[1,1,2]
    assert value['cursor']==(0,0,128)
    assert [(step,v['learning_rate']) for step,v in logged if 'train_loss' in v]==pytest.approx(
        [(1,3e-4),(2,5e-5),(3,2.7e-4),(4,4.5e-5),(5,2.43e-4),(6,4.05e-5)])
    assert len([v for step,v in logged if 'validation_mse' in v])==3
    assert not (root/'interval-results.json').exists()


def test_crash_after_boundary_update_resumes_validation_before_any_new_update(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v1 import train as module
    units,args,fresh=setup(tmp_path);contract={'test':'boundary-crash'}
    full=tmp_path/'full';full.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(full),Reporter())=='complete'
    expected=load_checkpoint(full/'checkpoint_last.pt',contract)
    stopped=tmp_path/'stopped';stopped.mkdir();original=module.save_checkpoint;once=[True]
    def interrupt(path,state):
        original(path,state)
        if state['step']==2 and state['phase']=='train' and once[0]:
            once[0]=False;raise KeyboardInterrupt
    monkeypatch.setattr(module,'save_checkpoint',interrupt);m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(stopped),Reporter())=='interrupted_preserved_last_checkpoint'
    partial=load_checkpoint(stopped/'checkpoint_last.pt',contract)
    assert partial['step']==2 and partial['epoch']==0 and partial['phase']=='train'
    m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(stopped,stopped/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(stopped/'checkpoint_last.pt',contract)
    for k in ('step','epoch','cursor','history','scheduler','data_pass','last_validation_step'):
        assert actual[k]==expected[k],k
    assert torch.equal(actual['rng']['torch'],expected['rng']['torch'])
    for k,v in actual['model'].items():assert torch.equal(v,expected['model'][k])
    for k,row in actual['optimizer']['state'].items():
        for name,v in row.items():assert torch.equal(v,expected['optimizer']['state'][k][name])


def test_cli_single_interval_and_restart_policy_bound(tmp_path):
    from research.bar_gpt.phase2_v1.schedule import schedule_manifest
    base=['--dataset-root','unused','--output',str(tmp_path/'run')]
    a=arguments(base);manifest=schedule_manifest(a)
    assert a.validation_every==150000 and a.lr_restart_decay==.9 and a.warmup_updates==2000
    assert manifest['cycle_updates']==a.validation_every
    assert manifest['planned_optimizer_updates']==1500000
    for extra in (['--lr-restart-decay','0'],['--lr-restart-decay','nan'],['--validation-every','2000']):
        with pytest.raises(SystemExit):arguments(base+extra)
