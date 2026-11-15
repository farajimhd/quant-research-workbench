import torch
from research.bar_gpt.phase2_v2.collapse_study import make_model
from research.bar_gpt.phase2_v2.model import future_loss


def test_narrow_variants_keep_future_mask_and_full_eval():
    torch.set_num_threads(1)
    x=torch.randn(3,16,512)
    y=torch.randn(3,512,requires_grad=True)
    ids=torch.arange(192)
    for name in ('narrow','stable','coordinate'):
        model=make_model(name)
        state,pred=model.forward_visible(x,None,ids)
        assert state.shape==pred.shape==(3,512)
        pred.retain_grad()
        future_loss(pred,y,ids).backward()
        assert y.grad is None
        assert not pred.grad[:,:192].any()
        assert pred.grad[:,192:].abs().sum()>0
        assert model.layers[0].self_attn.embed_dim==16
        assert model.layers[0].self_attn.num_heads==1
        with torch.no_grad():
            full=model.forward_visible(x,None,torch.arange(512))[1]
        assert full.shape==(3,512) and torch.isfinite(full).all()


def test_coordinate_identity_and_context_affect_output():
    torch.manual_seed(7)
    model=make_model('coordinate')
    x=torch.randn(4,16,512)
    ids=torch.arange(192)
    a=model.forward_visible(x,None,ids)[1]
    b=model.forward_visible(x.flip(0),None,ids)[1]
    assert not torch.allclose(a,b)


def test_coordinate_readout_mixed_precision_scatter():
    model=make_model('coordinate')
    with torch.autocast('cpu',dtype=torch.bfloat16):
        state,pred=model.forward_visible(torch.randn(2,16,512),None,torch.arange(192))
    assert state.dtype==pred.dtype==torch.bfloat16
    assert not state[:,192:].any()


def test_content_readout_cancels_coordinate_only_offsets():
    model=make_model('content')
    x=torch.zeros(3,16,512)
    for ids in (torch.arange(192),torch.arange(512)):
        with torch.autocast('cpu',dtype=torch.bfloat16):
            state,pred=model.forward_visible(x,None,ids)
        assert state.shape==(3,512) and not state.any()


def test_user_unmasked_baseline_attends_sixteen_complete_tokens():
    model=make_model('baseline')
    x=torch.randn(2,16,512)
    ids=model.draw_feature_ids('cpu')
    assert torch.equal(ids,torch.arange(512))
    state,pred=model.forward_visible(x,None,ids)
    assert state.shape==pred.shape==(2,512)
    pred.square().mean().backward()
    assert model.attention.self_attn.in_proj_weight.grad.abs().sum()>0
