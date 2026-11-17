"""CPU integrity tests for the real encoder/decoder and training batch path."""
import unittest
import torch
from .model import Config, Autoencoder, Transform, objective, PRICES, ACTIVITY
from .train import batch


class Contracts(unittest.TestCase):
    def test_inverse_difficult_values(self):
        raw=torch.tensor([1e-8,.1,.2,1.,1e4,1e12]).reshape(6,1,1).expand(6,64,18).clone()
        raw[0,:,ACTIVITY]=0
        valid=torch.ones_like(raw,dtype=torch.bool)
        trans=Transform(torch.zeros(18),torch.ones(18))
        out=trans.inverse(Transform.logs(raw,valid).float())
        torch.testing.assert_close(out,raw.double(),rtol=2e-6,atol=1e-10)

    def test_embedding_only_and_hidden_independence(self):
        for variant in ('pooled','scale_slots'):
            model=Autoencoder(Config(variant=variant,layers=1),torch.zeros(18),torch.ones(18)).eval()
            x=torch.randn(2,64,27);v=torch.ones_like(x,dtype=torch.bool)
            data=dict(values=x,valid=v,target=x[...,:18].clone(),mask=v[...,:18].clone())
            gen=torch.Generator().manual_seed(8)
            a,b,p,y,m=batch(data,torch.arange(2),16,gen)
            z,out=model(a,b,p)
            self.assertEqual(z.shape,(2,256));self.assertEqual(out.shape,(2,64,18))
            torch.testing.assert_close(model.decode(z),out)
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


if __name__=='__main__':unittest.main()
