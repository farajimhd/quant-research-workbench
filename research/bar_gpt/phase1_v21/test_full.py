import copy
import torch
from .full_train import learning_rate
from ..phase2_v2.train import save_checkpoint, load_checkpoint, rng_state, restore_rng


def test_schedule_endpoints_and_monotonic_decay():
    args=(10000,1e-4,1e-6,1000)
    assert learning_rate(0,*args)==1e-6
    assert learning_rate(1000,*args)==1e-4
    assert learning_rate(10000,*args)==1e-6
    rates=[learning_rate(x,*args) for x in range(1000,10001,100)]
    assert all(a>=b for a,b in zip(rates,rates[1:]))


def test_quality_is_checkpoint_safe_native_float():
    import numpy as np
    from .full_train import quality
    m={'all_visible/raw_mae/1s/trade_close':np.float64(1),
       'masked/raw_mae/1s/trade_close':np.float64(1),
       'all_visible/candle/1s/trade/moving_direction_pct':np.float64(90),
       'all_visible/range/1s/trade/bin_accuracy_pct':np.float64(80)}
    assert type(quality(m,m)[0]) is float


def test_checkpoint_replays_next_update_exactly(tmp_path):
    torch.manual_seed(17);model=torch.nn.Linear(3,2);opt=torch.optim.AdamW(model.parameters(),lr=1e-4)
    def update(m,o):
        o.zero_grad();loss=m(torch.randn(4,3)).square().mean();loss.backward();o.step()
    update(model,opt)
    path=tmp_path/'checkpoint_last.pt';contract={'workers':16,'batch_size':8192,'schedule':'cosine'}
    state=dict(contract=contract,model=copy.deepcopy(model.state_dict()),optimizer=copy.deepcopy(opt.state_dict()),rng=rng_state(),step=1,cursor=(0,1))
    save_checkpoint(path,state);update(model,opt);expected=copy.deepcopy(model.state_dict())
    saved=load_checkpoint(path,contract);restored=torch.nn.Linear(3,2);restored.load_state_dict(saved['model'])
    optimizer=torch.optim.AdamW(restored.parameters(),lr=1e-4);optimizer.load_state_dict(saved['optimizer']);restore_rng(saved['rng'])
    update(restored,optimizer)
    for k,v in expected.items():torch.testing.assert_close(restored.state_dict()[k],v,rtol=0,atol=0)


def test_panel_narrow_and_failure():
    from rich.console import Console
    from .full_panel import render
    for width in (80,120):
        console=Console(width=width,record=True)
        with console.capture() as capture:console.print(render({'state':'failed','error':'loader error','step':123}))
        assert 'loader error' in capture.get()
