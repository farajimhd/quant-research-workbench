"""Equivalent attention math and bounded stream preparation lifecycle."""
from contextlib import nullcontext
from types import SimpleNamespace
import threading
import pytest
import torch
from research.bar_gpt.phase2_v1.config import Config
from research.bar_gpt.phase2_v1.model import Forecaster
from research.bar_gpt.phase2_v1.device_pipeline import device_batches
from research.bar_gpt.phase2_v1.train import optimizer_for


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_batch_first_attention_same_parameters_rng_equations_and_gradients():
    torch.manual_seed(9);a=Forecaster(Config(layers=2,feedforward=64,attention_layout='torch')).double()
    after=torch.get_rng_state().clone()
    torch.manual_seed(9);b=Forecaster(Config(layers=2,feedforward=64,attention_layout='batch-first-sdpa')).double()
    assert torch.equal(after,torch.get_rng_state())
    assert a.state_dict().keys()==b.state_dict().keys()
    assert all(torch.equal(v,b.state_dict()[k]) for k,v in a.state_dict().items())
    x=torch.randn(2,16,512,dtype=torch.float64);meta=torch.randn(2,16,9,dtype=torch.float64)
    mask=torch.zeros(512,dtype=torch.bool);mask[:103]=True
    pa=a(x,meta,mask)[1];pb=b(x,meta,mask)[1]
    torch.testing.assert_close(pa,pb,rtol=1e-11,atol=1e-12)
    pa.square().mean().backward();pb.square().mean().backward()
    for (_,p),(_,q) in zip(a.named_parameters(),b.named_parameters()):
        torch.testing.assert_close(p.grad,q.grad,rtol=1e-10,atol=1e-12)


@pytest.fixture
def fake_cuda(monkeypatch):
    from research.bar_gpt.phase2_v1 import device_pipeline as module
    state=dict(synced=0,waited=0)
    class Stream:
        def __init__(self,*a,**kw):pass
        def synchronize(self):state['synced']+=1
        def wait_event(self,event):state['waited']+=1
    class Event:
        def record(self,stream):pass
    monkeypatch.setattr(module.torch.cuda,'Stream',Stream)
    monkeypatch.setattr(module.torch.cuda,'Event',Event)
    monkeypatch.setattr(module.torch.cuda,'device',lambda *a:nullcontext())
    monkeypatch.setattr(module.torch.cuda,'stream',lambda *a:nullcontext())
    monkeypatch.setattr(module.torch.cuda,'current_stream',lambda *a:Stream())
    return state


def test_stream_queue_order_bounded_early_close_and_worker_join(fake_cuda):
    state=dict(produced=0,consumed=0,max_ahead=0,closed=False)
    def source():
        try:
            for i in range(10):
                state['produced']+=1
                state['max_ahead']=max(state['max_ahead'],state['produced']-state['consumed'])
                yield (torch.tensor(i),dict(raw=torch.tensor(i+10)))
        finally:state['closed']=True
    stream=device_batches(source(),'cuda',True)
    for i in range(3):
        row=next(stream);state['consumed']+=1
        assert row[0].item()==i and row[1]['raw'].item()==i+10
    stream.close()
    assert state['closed'] and state['max_ahead']<=2
    assert fake_cuda['synced']==1 and fake_cuda['waited']==3
    assert not any(t.name.startswith('phase2-tokenize') for t in threading.enumerate())


def test_worker_exception_propagates_and_cleans_source(fake_cuda):
    closed=[]
    def source():
        try:
            yield torch.tensor(0)
            raise ValueError('fixture source mutation')
        finally:closed.append(True)
    stream=device_batches(source(),'cuda',True);next(stream)
    with pytest.raises(ValueError,match='source mutation'):next(stream)
    assert closed==[True] and fake_cuda['synced']==1


def test_unconsumed_worker_error_is_not_hidden_by_early_close(fake_cuda):
    ready=threading.Event()
    def source():
        yield torch.tensor(0)
        ready.set()
        raise ValueError('unconsumed corrupt input')
    stream=device_batches(source(),'cuda',True);next(stream)
    assert ready.wait(5)
    with pytest.raises(ValueError,match='unconsumed corrupt'):stream.close()


def test_disabled_prefetch_and_gpu_flags_fail_closed():
    from research.bar_gpt.phase2_v1.run_train import arguments
    assert list(device_batches(iter([1,2]),'cpu',False))==[1,2]
    with pytest.raises(ValueError,match='requires CUDA'):next(device_batches(iter([1]),'cpu',True))
    with pytest.raises(ValueError,match='requires CUDA'):
        optimizer_for(Forecaster(Config(layers=1,feedforward=64)),SimpleNamespace(
            device='cpu',learning_rate=3e-4,optimizer_backend='fused'))
    base=['--dataset-root','fixture','--output','D:/TradingML/runtimes/fixture']
    with pytest.raises(SystemExit):arguments(base+['--gpu-prefetch'])
    with pytest.raises(SystemExit):arguments(base+['--optimizer-backend','fused'])


@pytest.mark.skipif(not torch.cuda.is_available(),reason='native GPU qualification')
def test_real_cuda_stream_values_rng_and_shared_gradient_adamw():
    cpu=torch.get_rng_state().clone();cuda=torch.cuda.get_rng_state().clone()
    def source():
        for i in range(8):yield (torch.arange(512,device='cuda')+i,dict(raw=torch.full((64,18),i,device='cuda')))
    stream=device_batches(source(),'cuda',True)
    for i,row in enumerate(stream):
        assert torch.equal(row[0],torch.arange(512,device='cuda')+i)
        assert (row[1]['raw']==i).all()
    assert torch.equal(cpu,torch.get_rng_state()) and torch.equal(cuda,torch.cuda.get_rng_state())
    a=torch.nn.Parameter(torch.linspace(-.1,.1,4096,device='cuda'));b=torch.nn.Parameter(a.detach().clone())
    oa=torch.optim.AdamW([a],lr=3e-4,weight_decay=.01)
    ob=torch.optim.AdamW([b],lr=3e-4,weight_decay=.01,fused=True)
    for i in range(8):
        grad=torch.linspace(-.01,.01,4096,device='cuda')*(i+1)
        a.grad=grad.clone();b.grad=grad.clone();oa.step();ob.step()
    torch.testing.assert_close(a,b,rtol=1e-6,atol=1e-7)
    for key in ('exp_avg','exp_avg_sq','step'):
        torch.testing.assert_close(oa.state[a][key],ob.state[b][key],rtol=1e-6,atol=1e-7,check_device=False)
