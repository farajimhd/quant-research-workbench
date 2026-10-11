import pytest
import torch
from research.rl_trading.v6.transformer_search import specifications, TransformerTeacher, TransformerSpec, selection
from research.rl_trading.v6.architecture_benchmark import loss


def normalizers():
    return dict(mean=[0.]*147, std=[1.]*147), dict(mean=[0.]*35, std=[1.]*35)


def test_frozen_32_unique_scalable_specs():
    rows = specifications()
    assert len(rows) == len(set(rows)) == 32
    assert all(sum(r.family == family for r in rows) == 8 for family in {r.family for r in rows})
    for r in rows:
        assert r.width % r.heads == 0


@pytest.mark.parametrize('family', ['candle', 'patch', 'multiscale', 'latent_channel'])
def test_padding_invariance_empty_history_and_all_head_gradients(family):
    torch.set_num_threads(2); torch.manual_seed(17)
    model = TransformerTeacher(TransformerSpec(family, 32, 1, 4, 2, 4 if family in ('patch','multiscale') else 1), *normalizers())
    projected = torch.randn(4, 120, 32)
    present = torch.ones(4, 120, dtype=torch.bool); present[:, :91] = False
    altered = projected.clone(); altered[:, :91] = 999
    assert torch.equal(model.encoder.encode_history(projected, present), model.encoder.encode_history(altered, present))
    empty = model.encoder.encode_history(projected, torch.zeros_like(present))
    assert torch.isfinite(empty).all() and not empty.any()
    raw = torch.zeros(4,120,147); raw[:,:,35:37] = 1
    held = torch.zeros(4,11); held[2:,0] = 1
    future = torch.arange(4)[:,None].expand(4,5)
    data = dict(action=torch.arange(4), quality=torch.tensor([.7,0,0,.8]),
        ratio=torch.tensor([.5,float('nan'),float('nan'),float('nan')]),
        future=future, future_soft=torch.nn.functional.one_hot(future,4).float())
    context = model.representation(raw,present,held)
    value = loss(model,context,data,torch.ones(4)); assert torch.isfinite(value)
    value.backward()
    for module in (model.encoder.attention, model.heads.entry, model.heads.exit,
        model.heads.quality, model.size, model.forecast.reference_held, model.forecast_gru):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in module.parameters())
    probabilities = model.heads(context,held[:,0]>0).logits.softmax(-1)
    assert torch.allclose(probabilities.sum(-1),torch.ones(4))
    assert not probabilities[:2,2:].any() and not probabilities[2:,:2].any()
    with torch.no_grad():
        free = model.forecast(context,model.forecast_gru,steps=5).exp()
        assert torch.allclose(free.sum(-1),torch.ones(4,5))


@pytest.mark.parametrize('index', range(32))
def test_every_candidate_forward_is_finite(index):
    torch.set_num_threads(2)
    spec = specifications()[index]
    model = TransformerTeacher(spec,*normalizers())
    with torch.no_grad():
        output = model.encoder.encode_history(torch.randn(1,120,spec.width),torch.ones(1,120,dtype=torch.bool))
    assert output.shape == (1,spec.width) and torch.isfinite(output).all()


def test_selection_excludes_failed_gate_and_retains_quality_tradeoff():
    def row(name, ap, quality, passed=True):
        classes = {name:dict(f1=.3,average_precision=ap) for name in ('ENTRY','WAIT','HOLD','EXIT')}
        return dict(architecture=name,overfit_passed=passed,reload_exact=True,wandb_url='test',
            validation=dict(current=classes,forecast=[classes]*5,quality=dict(ENTRY=quality,EXIT=quality),allocation_mae=quality))
    result = selection([row('a',.1,.2),row('b',.2,.3),row('c',.01,.4),row('failed',.9,.01,False)])
    assert result['provisional_candidate']=='b'
    assert set(result['pareto_frontier'])=={'a','b'}
    assert result['eligible']==3 and not result['production_promotion']
