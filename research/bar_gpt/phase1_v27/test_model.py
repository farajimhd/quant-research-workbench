"""Guard identity initialization, temporal connectivity and the frozen boundary."""
from dataclasses import asdict
import torch
from .model import Autoencoder, Config
from ..phase0_v7.model import Autoencoder as Parent, Config as ParentConfig
from ..phase0_v7.test_model import example, parameters


def make():
    torch.manual_seed(17)
    parent = Parent(parameters(), ParentConfig())
    state = dict(parent.state_dict()); extra = state.pop("_extra_state")
    model = Autoencoder(parameters())
    model.load_phase0(dict(model=state, model_extra=extra, contract=dict(config=asdict(parent.config))))
    _, values = example(torch.float32)
    values = values.unsqueeze(1).expand(2,64,27).clone()
    valid = torch.ones_like(values,dtype=torch.bool)
    return parent,model,values,valid


def test_identity_and_exported_embedding_only_decoder():
    parent,model,values,valid = make()
    expected,actual = parent(values,valid),model(values,valid)
    assert actual['embedding'].shape == (2,64,32)
    torch.testing.assert_close(actual['logs'],expected['logs'],rtol=0,atol=0)
    torch.testing.assert_close(model.decode(actual['embedding'])['logs'],actual['logs'],rtol=0,atol=0)


def test_only_mixers_learn_and_frozen_decoder_passes_gradients():
    torch.set_num_threads(2)
    parent,model,values,valid = make()
    frozen = {n:p.clone() for n,p in model.named_parameters() if not p.requires_grad}
    assert all(n.startswith('mixers.') for n,p in model.named_parameters() if p.requires_grad)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=1e-4)
    target = parent(values,valid)['normalized'].detach() + .02
    losses=[]
    for _ in range(60):
        optimizer.zero_grad(set_to_none=True)
        loss=(model(values,valid)['normalized']-target).square().mean()
        loss.backward()
        assert all(torch.isfinite(p.grad).all() for p in model.mixers.parameters() if p.grad is not None)
        losses.append(float(loss.detach()));optimizer.step()
    assert losses[-1] < losses[0]*.5
    for n,p in model.named_parameters():
        if n in frozen:
            assert p.grad is None
            torch.testing.assert_close(p,frozen[n],rtol=0,atol=0)


def test_temporal_mlp_connects_distant_positions_without_sequence_flattening():
    _,model,_,_ = make()
    # Identity initialization deliberately has no temporal effect. Make one
    # temporal branch nonzero to test the architecture rather than that initial state.
    torch.nn.init.normal_(model.mixers[0].time[-1].weight,std=.01)
    z=torch.randn(1,64,32,requires_grad=True)
    model.mixers(z)[0,63].square().sum().backward()
    assert z.grad[0,0].abs().sum()>0
    assert z.grad[0,31].abs().sum()>0
