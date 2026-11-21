import torch
from .model import Autoencoder
from ..phase1_v12.model import Config, positions, gather


def test_only_learned_256_embedding_and_hidden_isolation():
    torch.set_num_threads(2)
    for arm in ('base','body','linear_body','slots_body','scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):
        torch.manual_seed(17)
        m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),arm).eval()
        z=torch.randn(2,64,16);metadata=torch.randn(2,64,36);pos=positions(2,'cpu',16)
        emb=m.encoder(gather(z,pos),gather(metadata,pos),pos)
        assert emb.shape==(2,256)
        pred=m.decode(emb);assert pred.shape==(2,64,16)
        other=z.clone();known=torch.zeros(2,64,dtype=torch.bool).scatter(1,pos,True)
        other[~known]=999
        torch.testing.assert_close(m(other,metadata,pos),pred,rtol=0,atol=0)
        (pred.square().mean()).backward()
        assert all(p.grad is None or torch.isfinite(p.grad).all() for p in m.parameters())


def test_temporal_slots_use_original_positions():
    torch.manual_seed(17);m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),'slots_body').eval()
    z=torch.randn(2,16,16);meta=torch.randn(2,16,36);pos=positions(2,'cpu',16)
    order=torch.randperm(16)
    torch.testing.assert_close(m.encoder(z,meta,pos),m.encoder(z[:,order],meta[:,order],pos[:,order]),rtol=1e-5,atol=1e-6)


def test_export_contains_no_decoder_or_extra_values(tmp_path):
    from .model import load_exported_encoder
    for arm in ('body','scaled_slots_price','scaled_slots_price_strong'):
        m=Autoencoder(Config(),torch.zeros(16),torch.ones(16),arm).eval()
        path=tmp_path/(arm+'.pt');torch.save(dict(config=vars(m.config),arm=arm,encoder=m.encoder.state_dict()),path)
        encoder=load_exported_encoder(path)
        assert not any('decoder' in k for k in encoder.state_dict())
        for n in (16,64):
            z=torch.randn(2,n,16);meta=torch.randn(2,n,36);pos=positions(2,'cpu',n)
            actual=encoder(z,meta,pos);assert actual.shape==(2,256)
            torch.testing.assert_close(actual,m.encoder(z,meta,pos),rtol=0,atol=0)
