import pytest
from research.rl_trading.v6.laptop_resources import LaptopGpuPacer


class Cuda:
    def synchronize(self, device):
        self.synchronized = device

    def mem_get_info(self, device):
        return self.free, 24*1024**3


def test_pacer_syncs_and_resets_after_pause():
    cuda = Cuda(); cuda.free = 8*1024**3
    now = [0.]; pauses = []
    def sleep(seconds):
        pauses.append(seconds); now[0] += seconds
    pacer = LaptopGpuPacer('cuda:0', clock=lambda:now[0], sleep=sleep, cuda=cuda)
    now[0] = 3.
    report = pacer()
    assert pauses == pytest.approx([1.])
    assert report['active_seconds'] == 3.
    assert cuda.synchronized == 'cuda:0'
    now[0] += 3.
    pacer()
    assert pauses == pytest.approx([1.,1.])


def test_reserve_fails_before_another_chunk():
    cuda = Cuda(); cuda.free = 3*1024**3
    pacer = LaptopGpuPacer('cuda:0', cuda=cuda, sleep=lambda _:pytest.fail('must stop'))
    with pytest.raises(RuntimeError, match='reserve breached'):
        pacer()


def test_prechunk_reserve_check_does_not_sleep_or_reset_clock():
    cuda = Cuda(); cuda.free = 8*1024**3
    now = [0.]; pauses = []
    pacer = LaptopGpuPacer('cuda:0', cuda=cuda, clock=lambda:now[0], sleep=pauses.append)
    now[0] = 3.
    assert pacer.check_reserve() == cuda.free
    assert not pauses and pacer.started == 0.
    pacer()
    assert pauses == pytest.approx([1.])


def test_encoder_stops_before_forward_when_reserve_is_low():
    import torch
    from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
    cuda=Cuda();cuda.free=3*1024**3
    model=TemporalCandleEncoder(16)
    model.resource_pacer=LaptopGpuPacer('cuda:0',cuda=cuda,sleep=lambda _:pytest.fail('must stop'))
    calls=[]
    handle=model.temporal.register_forward_pre_hook(lambda *_:calls.append(True))
    with pytest.raises(RuntimeError,match='reserve breached'):
        model.encode_history(torch.zeros(1,120,16))
    handle.remove()
    assert not calls


@pytest.mark.parametrize('duty', [0.,1.,float('nan'),float('inf')])
def test_invalid_duty(duty):
    with pytest.raises(ValueError):
        LaptopGpuPacer('cuda:0', duty_cycle=duty)


def test_history_microbatch_preserves_outputs_and_gradients():
    import copy
    import torch
    from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
    torch.set_num_threads(2);torch.manual_seed(17)
    full=TemporalCandleEncoder(16)
    bounded=copy.deepcopy(full);bounded.history_microbatch=2
    calls=[]
    bounded.resource_pacer=lambda:calls.append(True)
    x=torch.randn(5,120,16,requires_grad=True)
    y=x.detach().clone().requires_grad_()
    mask=torch.ones(5,120,dtype=torch.bool);mask[0,:100]=False;mask[1]=False
    a=full.encode_history(x,mask);b=bounded.encode_history(y,mask)
    assert len(calls)==3
    torch.testing.assert_close(a,b,rtol=1e-5,atol=1e-6)
    a.square().sum().backward();b.square().sum().backward()
    torch.testing.assert_close(x.grad,y.grad,rtol=1e-4,atol=1e-6)
    for p,q in zip(full.parameters(),bounded.parameters()):
        if p.grad is not None:torch.testing.assert_close(p.grad,q.grad,rtol=1e-4,atol=1e-5)
