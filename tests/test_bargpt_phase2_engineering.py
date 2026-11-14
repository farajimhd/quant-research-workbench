"""Packed latent coordinates and bounded profiling with actual trainer exclusion."""
from types import SimpleNamespace
import torch
import pytest
from research.bar_gpt.phase2_v1.model import Forecaster
from research.bar_gpt.phase2_v1.config import Config
from research.bar_gpt.phase2_v1 import benchmark
from research.bar_gpt.phase2_v1.release import certify
from tests.test_bargpt_phase2_all_release import dataset


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_retained_coordinates_packed_no_hidden_values_or_gradients():
    torch.manual_seed(17);m=Forecaster(Config(layers=1,feedforward=64)).train()
    x=torch.randn(3,16,512,requires_grad=True);meta=torch.zeros(3,16,9)
    mask=m.draw_mask('cpu');kept=(~mask).nonzero().flatten();seen=[]
    h=m.encoder.register_forward_pre_hook(lambda module,args:seen.append(args))
    _,pred=m(x,meta,mask);pred.square().mean().backward();h.remove()
    assert seen[0][0].shape==(3,16,len(kept))
    assert torch.equal(seen[0][2],kept)
    assert torch.equal(seen[0][0],x[:,:,kept])
    assert torch.count_nonzero(x.grad[:,:,mask])==0
    assert torch.count_nonzero(m.encoder.coordinates.weight.grad[:,mask])==0
    assert torch.count_nonzero(x.grad[:,:,~mask])>0
    altered=x.detach().clone();altered[:,:,mask]=float('nan')
    assert torch.isfinite(m(altered,meta,mask)[1]).all()
    with pytest.raises(ValueError,match='shared'):m(x,meta,torch.zeros(3,512,dtype=torch.bool))


def test_fresh_batch_masks_rng_restore_replays_exactly():
    m=Forecaster(Config(layers=1,feedforward=64)).train()
    state=torch.get_rng_state();first=m.draw_mask('cpu');second=m.draw_mask('cpu')
    assert not torch.equal(first,second)
    torch.set_rng_state(state)
    assert torch.equal(first,m.draw_mask('cpu')) and torch.equal(second,m.draw_mask('cpu'))


def test_production_uses_all_coordinates_and_drop_rate_is_configurable():
    from research.bar_gpt.phase2_v1.run_train import arguments
    model=Forecaster(Config(layers=1,feedforward=64));x=torch.randn(2,16,512);meta=torch.zeros(2,16,9)
    with pytest.raises(ValueError,match='eval'):model.encode_context(x,meta)
    model.eval();seen=[]
    h=model.encoder.register_forward_pre_hook(lambda module,args:seen.append(args[0].shape[-1]))
    state=model.encode_context(x,meta)
    with torch.no_grad():other=model(x,meta)[0]
    h.remove()
    assert torch.equal(state,other) and seen==[512,512]
    baseline=Forecaster(Config(layers=1,feedforward=64,mask_rate=0))
    assert not baseline.draw_mask('cpu').any()
    base=['--dataset-root','fixture','--output','D:/TradingML/runtimes/fixture']
    assert arguments(base+['--feature-drop-rate','0']).feature_drop_rate==0
    for rate in ('1','-0.1','nan'):
        with pytest.raises(SystemExit):arguments(base+['--feature-drop-rate',rate])


def test_profiler_rejects_actual_trainers_before_gpu_or_teacher_access(monkeypatch):
    monkeypatch.setattr(benchmark,'active_trainers',lambda:[dict(pid=123,created=456)])
    def forbidden(*a,**kw):raise AssertionError('GPU/teacher touched despite actual trainer')
    monkeypatch.setattr(benchmark,'select_training_teacher',forbidden)
    monkeypatch.setattr(torch.cuda,'is_available',forbidden)
    result=benchmark.profile_gpu(None)
    assert result['state']=='blocked_active_training' and result['gpu_work'] is False


def test_process_guard_uses_live_python_commands_not_stale_status(monkeypatch):
    processes=[SimpleNamespace(pid=20,info=dict(name='python.exe',pid=20,create_time=100,
                    cmdline=['python','run_encoder_v4.py','--output','fixture'])),
               SimpleNamespace(pid=21,info=dict(name='powershell.exe',pid=21,create_time=200,
                    cmdline=['powershell','-NoExit','run_encoder_v4.py'])),
               SimpleNamespace(pid=22,info=dict(name='python.exe',pid=22,create_time=300,
                    cmdline=['python','research.bar_gpt.phase2_v1.run_train','--mode','audit']))]
    monkeypatch.setattr(benchmark.psutil,'process_iter',lambda attrs:processes)
    assert benchmark.active_trainers()==[dict(pid=20,created=100)]


def test_cpu_benchmark_uses_sealed_samples_and_checks_all_used_hashes(tmp_path):
    u=dataset(tmp_path/'source');release=tmp_path/'release'
    cert=certify([u.root],release,workers=1)
    units,sha=benchmark.sample_units(release/'certificate.json')
    assert sha==cert['hash'] and len(units)==1
    rows=benchmark.profile_io(units)
    assert [r['io_workers'] for r in rows]==[1,2,4]
    assert all(r['rows'][0]['eligible_origins']==193 for r in rows)
    file=u.path/'origins.npy'
    with file.open('ab') as stream:stream.write(b'corrupt')
    with pytest.raises(ValueError,match='mutation'):benchmark.profile_io(units)
