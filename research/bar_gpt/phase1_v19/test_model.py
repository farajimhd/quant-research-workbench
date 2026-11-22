import torch
from .model import Autoencoder,load_exported_encoder
from ..phase1_v12.model import Config,positions,gather


def test_hidden_isolation_export_and_gradients(tmp_path):
    torch.set_num_threads(2)
    for arch in ('scaled','unscaled','centered','mean_filled','control_variate','mask_adapter'):
        torch.manual_seed(17)
        m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),arch).eval()
        z=torch.randn(2,64,16);meta=torch.randn(2,64,36);pos=positions(2,'cpu',16)
        embedding=m.encoder(gather(z,pos),gather(meta,pos),pos)
        assert embedding.shape==(2,256)
        expected=m.decode(embedding)
        hidden=~torch.zeros(2,64,dtype=torch.bool).scatter(1,pos,True)
        changed=z.clone();changed[hidden]=10000
        changed_meta=meta.clone();changed_meta[hidden]=-10000
        torch.testing.assert_close(m(changed,changed_meta,pos),expected,rtol=0,atol=0)
        expected.square().mean().backward()
        assert all(p.grad is None or torch.isfinite(p.grad).all() for p in m.parameters())
        path=tmp_path/(arch+'.pt')
        torch.save(dict(config=vars(m.config),architecture=arch,encoder=m.encoder.state_dict()),path)
        exported=load_exported_encoder(path)
        assert not any('decoder' in k for k in exported.state_dict())
        for count in (16,64):
            p=positions(2,'cpu',count)
            torch.testing.assert_close(exported(gather(z,p),gather(meta,p),p),m.encoder(gather(z,p),gather(meta,p),p),rtol=0,atol=0)


def test_mean_fill_preserves_all_visible_parent_exactly():
    parent=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'scaled').eval()
    fixed=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'mean_filled').eval()
    fixed.load_state_dict(parent.state_dict())
    z=torch.randn(3,64,16);meta=torch.randn(3,64,36);pos=positions(3,'cpu',64)
    torch.testing.assert_close(parent(z,meta,pos),fixed(z,meta,pos),rtol=0,atol=0)
    variate=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'control_variate').eval()
    variate.load_state_dict(parent.state_dict())
    torch.testing.assert_close(parent(z,meta,pos),variate(z,meta,pos),rtol=0,atol=0)


def test_bottleneck_expansion_preserves_prediction_and_can_learn():
    from .model import initialize_parent
    parent=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'scaled').eval()
    larger=Autoencoder(Config(embedding=512),torch.zeros(16),torch.ones(16),'scaled').eval()
    changed=initialize_parent(larger,parent.state_dict())
    assert len(changed)==3
    z=torch.randn(2,64,16);meta=torch.randn(2,64,36)
    for count in (16,64):
        pos=positions(2,'cpu',count)
        torch.testing.assert_close(parent(z,meta,pos),larger(z,meta,pos),rtol=1e-5,atol=1e-6)
    larger(z,meta,pos).square().mean().backward()
    assert larger.decoder.weight.grad[:,256:].abs().sum()>0


def test_mask_adapter_warmup_cannot_change_all_visible():
    from .model import initialize_parent
    torch.manual_seed(17)
    parent=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'mean_filled').eval()
    m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'mask_adapter').eval()
    initialize_parent(m,parent.state_dict());m.requires_grad_(False);m.encoder.mask_adapter.requires_grad_(True)
    z=torch.randn(2,64,16);meta=torch.randn(2,64,36);full=positions(2,'cpu',64);masked=positions(2,'cpu',16)
    baseline=m(z,meta,full).detach().clone();initial=m(z,meta,masked).detach().clone()
    optimizer=torch.optim.AdamW(m.encoder.mask_adapter.parameters(),lr=1e-3)
    m(z,meta,masked).square().mean().backward();optimizer.step()
    torch.testing.assert_close(m(z,meta,full),baseline,rtol=0,atol=0)
    assert not torch.equal(m(z,meta,masked),initial)


def test_count_head_protects_non_count_coordinates():
    from types import SimpleNamespace
    from .model import initialize_parent
    torch.manual_seed(19)
    parent=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'mask_adapter')
    m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'count_nullspace')
    initialize_parent(m,parent.state_dict())
    teacher=SimpleNamespace(decoder=torch.nn.Linear(16,18),scale=torch.ones(18))
    m.initialize_count_basis(teacher);m.requires_grad_(False);m.count_head.requires_grad_(True)
    e=torch.randn(8,256);before=teacher.decoder(m.decode(e)).detach()
    opt=torch.optim.AdamW(m.count_head.parameters(),lr=.01)
    for _ in range(3):
        opt.zero_grad();code=teacher.decoder(m.decode(e));(code[...,11]-2).square().mean().backward();opt.step()
    after=teacher.decoder(m.decode(e));keep=[i for i in range(18) if i not in (5,11,17)]
    # Two FP32 matrix multiplies introduce rounding despite the FP64 nullspace.
    torch.testing.assert_close(after[...,keep],before[...,keep],rtol=1e-5,atol=5e-6)
    assert (after[...,11]-before[...,11]).abs().max()>.01


def test_split_gradient_updates_preserve_inactive_weights():
    from .model import gradient_scope
    m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'mask_adapter')
    z=torch.randn(2,64,16);meta=torch.randn(2,64,36)
    opt=torch.optim.AdamW(m.parameters(),lr=.001)
    for count in (16,64,16):
        gradient_scope(m,count);before={k:p.detach().clone() for k,p in m.named_parameters()}
        opt.zero_grad(set_to_none=True);m(z,meta,positions(2,'cpu',count)).square().mean().backward();opt.step()
        for k,p in m.named_parameters():
            active=('mask_adapter' in k)==(count==16)
            if not active:torch.testing.assert_close(p,before[k],rtol=0,atol=0)
