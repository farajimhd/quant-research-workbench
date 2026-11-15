"""Ordered publication preparation, cancellation, exact state and CUDA replay."""
import copy
import threading
import time
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_v1.train import batches,train,load_checkpoint,autocast
from research.bar_gpt.phase2_v1.model import Forecaster,future_loss
from research.bar_gpt.phase2_v1.config import Config
from research.bar_gpt.phase2_v1.cuda_step import CudaStep
from research.bar_gpt.phase2_v1.run_train import arguments
from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def args(root,resume=None,workers=2):
    a=options(root,resume);a.prepare_workers=workers;a.io_workers=2;a.prefetch_mib=256
    a.pack_batches=True;a.batch_size=256;a.epochs=1
    return a


def test_order_exact_targets_metadata_evidence_rng_and_cold_cursor(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v1 import publication_pipeline as module
    units=[session(tmp_path/'slow'),session(tmp_path/'fast','2023-01-04')]
    teacher=Teacher();a=args(tmp_path,workers=0)
    expected=list(batches(units,0,teacher,'pinned',a,reconstruction=True))
    original=module.plan_block
    def delayed(reader,index):
        if 'slow' in reader.directory.parts:time.sleep(.05)
        return original(reader,index)
    monkeypatch.setattr(module,'plan_block',delayed);a.prepare_workers=2
    before=torch.get_rng_state().clone()
    actual=list(batches(units,0,teacher,'pinned',a,reconstruction=True))
    assert torch.equal(before,torch.get_rng_state())
    for old,new in zip(expected,actual):
        assert old[3]==new[3]
        for i in range(3):assert torch.equal(old[i],new[i])
        for i in (4,5):assert np.array_equal(old[i],new[i])
        for key in old[6]:assert torch.equal(old[6][key],new[6][key])
    suffix=list(batches(units,0,teacher,'pinned',a,cursor=actual[0][3]))
    assert torch.equal(suffix[0][2],actual[1][2])
    assert not any(t.name.startswith('phase2-publication') for t in threading.enumerate())


def test_unconsumed_failure_is_propagated_and_workers_join(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v1 import publication_pipeline as module
    units=[session(tmp_path/'good'),session(tmp_path/'bad','2023-01-04')]
    failed=threading.Event();original=module.plan_block
    def broken(reader,index):
        if 'bad' in reader.directory.parts:failed.set();raise ValueError('unconsumed publication failure')
        return original(reader,index)
    monkeypatch.setattr(module,'plan_block',broken)
    a=args(tmp_path);a.pack_batches=False;a.batch_size=64
    stream=batches(units,0,Teacher(),'pinned',a);next(stream)
    assert failed.wait(3)
    with pytest.raises(ValueError,match='unconsumed'):stream.close()
    assert not any(t.name.startswith('phase2-publication') for t in threading.enumerate())


def test_serial_parallel_and_graceful_resume_fullstate_exact(tmp_path):
    units=[session(tmp_path/'a'),session(tmp_path/'b','2023-01-04'),session(tmp_path/'v','2026-01-05')]
    contract={'test':'publication-resume'}
    def fresh():
        torch.manual_seed(7);return Forecaster(Config(layers=1,feedforward=64)),Teacher()
    serial=tmp_path/'serial';serial.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(serial,workers=0),Reporter())=='complete'
    expected=load_checkpoint(serial/'checkpoint_last.pt',contract)
    resumed=tmp_path/'resumed';resumed.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(resumed),Reporter(resumed,1))=='stopped'
    (resumed/'STOP').unlink()  # Only the fixture-owned marker, never a launcher action.
    m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(resumed,resumed/'checkpoint_last.pt'),Reporter())=='complete'
    assert state_hash(expected)==state_hash(load_checkpoint(resumed/'checkpoint_last.pt',contract))


def test_cli_execution_flags_are_bounded_and_explicit():
    base=['--dataset-root','fixture','--output','D:/TradingML/runtimes/fixture']
    assert arguments(base).prepare_workers==0 and not arguments(base).cuda_step_replay
    for more in (['--prepare-workers','9'],['--cuda-step-replay']):
        with pytest.raises(SystemExit):arguments(base+more)
    a=arguments(base+['--mode','train','--execute','--device','cuda','--precision','bf16',
                      '--cuda-step-replay','--prepare-workers','4'])
    assert a.cuda_step_replay and a.prepare_workers==4


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_coordinate_ids_match_original_mask_and_exact_rng(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('Native CUDA qualification')
    m=Forecaster(Config(layers=1,feedforward=64))
    for _ in range(8):
        cpu=torch.get_rng_state();gpu=torch.cuda.get_rng_state_all() if device=='cuda' else []
        mask=m.draw_mask(device);after_cpu=torch.get_rng_state()
        after_gpu=torch.cuda.get_rng_state_all() if device=='cuda' else []
        torch.set_rng_state(cpu)
        if gpu:torch.cuda.set_rng_state_all(gpu)
        ids=m.draw_feature_ids(device)
        assert torch.equal(ids,(~mask).nonzero().flatten())
        assert torch.equal(after_cpu,torch.get_rng_state())
        assert all(torch.equal(a,b) for a,b in zip(after_gpu,torch.cuda.get_rng_state_all())) if gpu else True


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Native CUDA qualification')
def test_cuda_replay_exact_mask_rng_gradients_optimizer_and_repeat_updates():
    torch.manual_seed(19);a=Forecaster(Config(layers=1,feedforward=64)).cuda()
    initial=copy.deepcopy(a.state_dict());x=torch.randn((8,16,512),device='cuda')
    metadata=torch.randn((8,16,9),device='cuda');target=torch.randn((8,512),device='cuda')
    cpu=torch.get_rng_state();gpu=torch.cuda.get_rng_state_all();results=[]
    for captured in (False,True):
        m=Forecaster(Config(layers=1,feedforward=64)).cuda();m.load_state_dict(initial)
        opt=torch.optim.AdamW(m.parameters(),lr=3e-4,fused=True)
        replay=CudaStep(m,8,memory_mib=2048) if captured else None
        torch.set_rng_state(cpu);torch.cuda.set_rng_state_all(gpu)
        for _ in range(8):
            if replay:p,loss,norm=replay(x,metadata,target)
            else:
                opt.zero_grad(set_to_none=True)
                with torch.autocast('cuda',dtype=torch.bfloat16):_,p=m(x,metadata);loss=future_loss(p,target)
                loss.backward();norm=torch.nn.utils.clip_grad_norm_(m.parameters(),1.,error_if_nonfinite=True)
            assert torch.isfinite(loss) and torch.isfinite(norm)
            opt.step()
        results.append(state_hash(dict(model=m.state_dict(),optimizer=opt.state_dict(),
                    cpu_rng=torch.get_rng_state(),gpu_rng=torch.cuda.get_rng_state_all())))
    assert results[0]==results[1]
