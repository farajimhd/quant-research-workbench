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


@pytest.mark.parametrize('duty', [0.,1.,float('nan'),float('inf')])
def test_invalid_duty(duty):
    with pytest.raises(ValueError):
        LaptopGpuPacer('cuda:0', duty_cycle=duty)
