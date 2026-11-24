"""No hidden-bar leakage; decoder remains frozen while encoder can learn."""
import torch
from .model import Autoencoder
from .objectives import objective,direction_loss
from ..phase1_v27.model import Autoencoder as Parent
from ..phase0_v7.test_model import example,parameters


def make():
    torch.set_num_threads(2)
    torch.manual_seed(17)
    parent=Parent(parameters())
    state=dict(parent.state_dict());extra=state.pop('_extra_state')
    model=Autoencoder(parameters());model.load_previous(dict(model=state,model_extra=extra))
    _,values=example(torch.float32)
    values=values.unsqueeze(1).expand(16,64,27).clone()
    return parent,model,values,torch.ones_like(values,dtype=torch.bool)


def test_visible_parent_identity_and_hidden_input_independence():
    parent,model,values,valid=make()
    model.eval();parent.eval()
    torch.testing.assert_close(model(values,valid)['logs'],parent(values,valid)['logs'],rtol=0,atol=0)
    model.train();torch.manual_seed(1);out=model(values,valid)
    counts=out['hidden'].sum(1)
    assert counts.min()>=4 and counts.max()<=8 and counts.unique().numel()>1
    altered=values.clone();altered[out['hidden']]+=.2
    torch.manual_seed(1);other=model(altered,valid)
    torch.testing.assert_close(out['logs'],other['logs'],rtol=0,atol=0)


def test_encoder_and_mixer_receive_gradients_but_decoder_is_frozen():
    _,model,values,valid=make()
    loss,parts=objective(model,model(values,valid),values,valid)
    loss.backward()
    assert model.stem[0].weight.grad.abs().sum()>0
    assert model.mixers[0].feature[-1].weight.grad.abs().sum()>0
    assert model.mask_token.grad.abs().sum()>0
    assert model.decoder.weight.grad is None
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)


def test_direction_hinge_zero_at_truth_and_penalizes_wrong_sign():
    target=torch.tensor([-1.,0.,1.])
    valid=torch.ones(3,dtype=torch.bool)
    assert direction_loss(target,target,valid,.1)==0
    predicted=(-target).requires_grad_()
    loss=direction_loss(predicted,target,valid,.1);loss.backward()
    assert loss>0 and predicted.grad[0]>0 and predicted.grad[2]<0


def test_structural_penalties_have_gradients_and_source_exceptions_are_explicit():
    from ..phase0_v7.transforms import coordinates
    _,model,values,valid=make();model.eval()
    code,mask=coordinates(values[...,:18],valid[...,:18])
    logs=values[...,:18].clone();logs[...,1]=logs[...,0]-.1;logs.requires_grad_()
    out=dict(logs=logs,code=code,normalized=(code-model.mean[:18])/model.scale[:18])
    _,parts=objective(model,out,values,valid)
    assert parts['ohlc']>0 and parts['range']>0
    parts['ohlc'].backward();assert logs.grad[...,1].abs().sum()>0
    inconsistent=values.clone();inconsistent[...,1]=inconsistent[...,0]-.1
    _,parts=objective(model,out,inconsistent,valid)
    assert parts['source_inconsistent_ohlc']>0
