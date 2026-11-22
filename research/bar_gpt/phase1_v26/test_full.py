import copy
import torch
from .full_train import learning_rate
from ..phase2_v2.train import save_checkpoint,load_checkpoint,rng_state,restore_rng


def test_window_schedule_and_exact_state_recovery(tmp_path):
    assert learning_rate(0,10000,1.,.01,500)==.01
    assert learning_rate(500,10000,1.,.01,500)==1.
    assert learning_rate(10000,10000,1.,.01,500)==.01
    rates=[learning_rate(x,10000,1.,.01,500) for x in range(500,10001,500)]
    assert all(a>=b for a,b in zip(rates,rates[1:]))
    torch.manual_seed(917);m=torch.nn.Linear(3,2);o=torch.optim.AdamW(m.parameters(),lr=1e-6)
    def step(model,opt):
        opt.zero_grad();model(torch.randn(4,3)).square().mean().backward();opt.step()
    step(m,o);contract=dict(batch_size=4096,workers=8,epochs=20)
    path=tmp_path/'checkpoint_last.pt';save_checkpoint(path,dict(model=copy.deepcopy(m.state_dict()),optimizer=copy.deepcopy(o.state_dict()),rng=rng_state(),contract=contract,step=1,cursor=(0,1)))
    step(m,o);expected=copy.deepcopy(m.state_dict());s=load_checkpoint(path,contract)
    m.load_state_dict(s['model']);o.load_state_dict(s['optimizer']);restore_rng(s['rng']);step(m,o)
    for k,v in m.state_dict().items():torch.testing.assert_close(v,expected[k],rtol=0,atol=0)


def test_panel_exposes_units_quality_and_failure():
    from rich.console import Console
    from .full_panel import render
    for width in (80,120):
        console=Console(width=width)
        with console.capture() as captured:console.print(render(dict(state='failed',error='loader failure',cheap_price_median_pct=315.2)))
        text=captured.get();assert 'loader failure' in text and '315.2%' in text and 'windows/s' in text
