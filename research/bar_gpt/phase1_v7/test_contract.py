"""CPU integrity tests for the real encoder/decoder and training batch path."""
import unittest
import tempfile
from pathlib import Path
import torch
from .model import Config, Autoencoder, Transform, objective, PRICES, ACTIVITY
from .train import batch, save_checkpoint
from .export import EncoderOnly



class Contracts(unittest.TestCase):
    def test_inverse_difficult_values(self):
        raw=torch.tensor([1e-8,.1,.2,1.,1e4,1e12]).reshape(6,1,1).expand(6,64,18).clone()
        raw[0,:,ACTIVITY]=0
        valid=torch.ones_like(raw,dtype=torch.bool)
        trans=Transform(torch.zeros(18),torch.ones(18))
        out=trans.inverse(Transform.logs(raw,valid).float())
        torch.testing.assert_close(out,raw.double(),rtol=2e-6,atol=1e-10)

    def test_embedding_only_and_hidden_independence(self):
        for variant in ('canonical','geometry'):
            model=Autoencoder(Config(variant=variant,width=64,layers=1),torch.zeros(18),torch.ones(18)).eval()
            x=torch.ones(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
            data=dict(values=x,valid=v,target=x[...,:18].clone(),mask=v[...,:18].clone())
            gen=torch.Generator().manual_seed(8)
            a,b,p,y,m=batch(data,torch.arange(2),16,gen)
            z,out=model(a,b,p)
            encoder=EncoderOnly(model)
            self.assertFalse(hasattr(encoder,'decoder'))
            torch.testing.assert_close(encoder(a,b,p),z)
            self.assertEqual(z.shape,(2,512));self.assertEqual(out.shape,(2,64,18))
            torch.testing.assert_close(model.decode(z),out)
            raw=model.transform.inverse(out)
            self.assertTrue((raw[...,ACTIVITY]>=0).all())
            for base in (0,6,12):
                o,h,l,c=raw[...,base:base+4].unbind(-1)
                self.assertTrue((h>=torch.maximum(o,c)).all())
                self.assertTrue((l<=torch.minimum(o,c)).all())
            self.assertEqual(int(m.sum()),2*48*18)
            # Alter every hidden target; exported embedding and decoder stay fixed.
            data['target']+=1000
            torch.testing.assert_close(model(a,b,p)[1],out)
            loss,_=objective(out,y,m,torch.ones(18));loss.backward()
            self.assertTrue(all(torch.isfinite(q.grad).all() for q in model.parameters() if q.grad is not None))

    def test_invalid_mask_has_zero_loss_gradient(self):
        pred=torch.randn(2,64,18,requires_grad=True);target=torch.zeros_like(pred)
        valid=torch.ones_like(pred,dtype=torch.bool);valid[:,:,6:]=False
        loss,_=objective(pred,target,valid,torch.ones(18));loss.backward()
        self.assertEqual(float(pred.grad[:,:,6:].abs().sum()),0.)

    def test_visible_validity_is_exported_without_hidden_metadata(self):
        model=Autoencoder(Config(variant='canonical',width=64,embedding=512,validity_slots=True),torch.zeros(18),torch.ones(18))
        x=torch.ones(2,16,27);v=torch.ones_like(x,dtype=torch.bool)
        v[0,:8,9]=False;v[0,:4,15]=False
        z=model.encode(x,v,torch.arange(16).expand(2,-1))
        encoder=EncoderOnly(model)
        self.assertFalse(hasattr(encoder,'decoder'))
        torch.testing.assert_close(encoder(x,v,torch.arange(16).expand(2,-1)),z)
        self.assertEqual(z.shape,(2,512))
        torch.testing.assert_close(z[:,18:21],torch.tensor([[.5,.75,.5],[1.,1.,1.]]))

    def test_checkpoint_resume_replays_next_update(self):
        config=Config(variant='canonical',width=64,layers=1)
        torch.manual_seed(37)
        model=Autoencoder(config,torch.zeros(18),torch.ones(18))
        opt=torch.optim.AdamW(model.parameters(),lr=.001)
        schedule=torch.optim.lr_scheduler.CosineAnnealingLR(opt,10)
        gen=torch.Generator().manual_seed(81)
        x=torch.ones(8,64,27);v=torch.ones_like(x,dtype=torch.bool)
        data=dict(values=x,valid=v,target=x[...,:18],mask=v[...,:18])
        def update(m,o,s,g):
            ids=torch.randint(8,(3,),generator=g)
            inputs,valid,pos,target,mask=batch(data,ids,16,g)
            o.zero_grad();_,out=m(inputs,valid,pos)
            loss,_=objective(out,target,mask,torch.ones(18));loss.backward();o.step();s.step()
            return loss.detach()
        update(model,opt,schedule,gen)
        root=Path('D:/TradingML/runtimes/bar_gpt')
        with tempfile.TemporaryDirectory(prefix='v7-resume-test-',dir=root) as folder:
            path=Path(folder)/'last.pt'
            save_checkpoint(path,model,opt,schedule,gen,1,0.,dict(test=True),1.)
            expected=update(model,opt,schedule,gen)
            restored=Autoencoder(config,torch.zeros(18),torch.ones(18))
            ro=torch.optim.AdamW(restored.parameters(),lr=.001)
            rs=torch.optim.lr_scheduler.CosineAnnealingLR(ro,10)
            state=torch.load(path,weights_only=False,map_location='cpu')
            restored.load_state_dict(state['model']);ro.load_state_dict(state['optimizer']);rs.load_state_dict(state['scheduler'])
            rg=torch.Generator();rg.set_state(state['generator'])
            actual=update(restored,ro,rs,rg)
            torch.testing.assert_close(actual,expected,rtol=0,atol=0)
            for left,right in zip(model.parameters(),restored.parameters()):torch.testing.assert_close(left,right,rtol=0,atol=0)


if __name__=='__main__':unittest.main()
