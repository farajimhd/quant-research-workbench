"""Negative shortcut tests, raw-unit invertibility, masking, and encoder-only parity."""
import inspect,tempfile
from pathlib import Path
import unittest
import numpy as np
import torch
from .model import Config,Autoencoder,coordinates,inverse,objective
from .data import cache,calibrate,batch
from .export import encoder_only,save,load
from ..v5.shards.phase1_contract import SAMPLE_DTYPE


def sample(n=4):
    r=np.zeros(n,dtype=SAMPLE_DTYPE);r['valid']=1;r['timeframe_us']=1000000
    r['end_us']=1700000000000000+np.arange(64)*1000000
    for i in range(n):
        base=.01*10**i;trend=np.linspace(.9,1.1,64)*base
        for offset in (0,6,12):
            r['values'][i,:,offset:offset+4]=trend[:,None]*np.array([1,1.05,.95,1.01])*(1.001 if offset==12 else 1)
            r['values'][i,:,offset+4:offset+6]=np.linspace(1,100,64)[:,None]
    r['valid'][0,:8,6:]=0
    return r


class Contracts(unittest.TestCase):
    def test_fidelity_auxiliary_never_penalizes_hidden_batches(self):
        data=cache(sample());params,_=calibrate(data,'joint')
        model=Autoencoder(Config(width=64,layers=1,embedding=32,output='joint'),**params)
        for full in (False,True):
            b=batch(data,torch.arange(4),16,torch.Generator().manual_seed(3),full)
            outputs=model(*b[:3])[1]
            base,_=objective(outputs,*b[3:],model.input_mean,model.input_scale,.02)
            auxiliary,_=objective(outputs,*b[3:],model.input_mean,model.input_scale,.02,.005)
            if full:self.assertGreater(float(auxiliary.detach()),float(base.detach()))
            else:torch.testing.assert_close(base,auxiliary,rtol=0,atol=0)

    def test_checkpoint_next_update_matches_uninterrupted(self):
        from .checkpoint import save_checkpoint
        data=cache(sample());params,_=calibrate(data,'joint')
        config=Config(architecture='fused',width=64,layers=1,embedding=32,output='joint')
        model=Autoencoder(config,**params);opt=torch.optim.AdamW(model.parameters(),lr=.001)
        scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,10)
        gen=torch.Generator().manual_seed(19)
        def update(m,o,s,g):
            ids=torch.multinomial(torch.tensor([1.,2.,3.,4.]),4,replacement=True,generator=g)
            b=batch(data,ids,16,g,False);o.zero_grad(set_to_none=True)
            loss,_=objective(m(*b[:3])[1],*b[3:],m.input_mean,m.input_scale,.02)
            loss.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),1.);o.step();s.step()
            return b
        update(model,opt,scheduler,gen)
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            path=Path(folder)/'last.pt';save_checkpoint(path,model,opt,scheduler,gen,1,0,{'test':True},1.)
            expected=update(model,opt,scheduler,gen)
            state=torch.load(path,weights_only=False)
            restored=Autoencoder(config,**params);restored.load_state_dict(state['model'])
            ro=torch.optim.AdamW(restored.parameters(),lr=.001);rs=torch.optim.lr_scheduler.CosineAnnealingLR(ro,10)
            ro.load_state_dict(state['optimizer']);rs.load_state_dict(state['scheduler'])
            rg=torch.Generator();rg.set_state(state['generator']);actual=update(restored,ro,rs,rg)
            for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0)
            for key,value in model.state_dict().items():torch.testing.assert_close(restored.state_dict()[key],value,rtol=0,atol=0)
            self.assertEqual(rs.state_dict(),scheduler.state_dict())

    def test_no_copied_latent_coordinates(self):
        data=cache(sample());params,_=calibrate(data,'joint')
        for architecture in ('mlp','attention','fused'):
            model=Autoencoder(Config(architecture=architecture,width=64,layers=1,embedding=32,output='joint'),**params)
            with torch.no_grad():
                for name,p in model.named_parameters():
                    if not name.startswith('decoder.'):p.zero_()
            positions=torch.arange(64).expand(4,-1)
            z,out=model(data['values'],data['valid'],positions)
            torch.testing.assert_close(z,torch.zeros_like(z),rtol=0,atol=0)
            for value in out:torch.testing.assert_close(value,value[:1].expand_as(value),rtol=0,atol=0)
        self.assertEqual(list(inspect.signature(Autoencoder.decode).parameters),['self','z'])

    def test_hidden_targets_never_enter_encoder(self):
        data=cache(sample());params,_=calibrate(data,'direct')
        for architecture in ('mlp','attention','fused'):
            model=Autoencoder(Config(architecture=architecture,width=64,layers=1,embedding=32),**params).eval()
            gen=torch.Generator().manual_seed(2);b=batch(data,torch.arange(4),16,gen)
            z=model.encode(*b[:3]);changed={k:v.clone() for k,v in data.items()}
            changed['values'][b[-1]]+=1000
            other=batch(changed,torch.arange(4),16,torch.Generator().manual_seed(2))
            torch.testing.assert_close(model.encode(*other[:3]),z,rtol=0,atol=0)
            self.assertEqual(int(b[-1].sum()),4*48)

    def test_encoder_only_and_finite_masked_gradients(self):
        data=cache(sample());params,_=calibrate(data,'joint')
        model=Autoencoder(Config(width=64,layers=1,embedding=32,output='joint'),**params)
        b=batch(data,torch.arange(4),16,torch.Generator().manual_seed(3))
        z,outputs=model(*b[:3]);loss,_=objective(outputs,*b[3:],model.input_mean,model.input_scale)
        loss.backward();self.assertTrue(all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None))
        encoder=encoder_only(model);self.assertFalse(hasattr(encoder,'decoder'))
        torch.testing.assert_close(encoder(*b[:3]),z,rtol=0,atol=0)
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            path=Path(folder)/'encoder.pt';save(model,path,test=True);restored=load(path)
            torch.testing.assert_close(restored(*b[:3]),z,rtol=0,atol=0)
            self.assertFalse(any('decoder' in key for key in restored.state_dict()))
            rows=sample()
            actual=restored.encode_raw(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
                torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()))
            expected=model.encode(data['values'],data['valid'],torch.arange(64).expand(4,-1))
            torch.testing.assert_close(actual,expected,rtol=0,atol=0)

    def test_structured_coordinate_oracle_roundtrip(self):
        # Labelled coordinate oracle tests algebra only, never model acceptance.
        rows=sample(1);rows['valid']=1;data=cache(rows)
        for kind in ('direct','geometry','joint'):
            params,_=calibrate(data,kind);model=Autoencoder(Config(width=64,layers=1,embedding=32,output=kind),**params)
            code,_=coordinates(data['values'][...,:18],data['valid'][...,:18],kind)
            with torch.no_grad():
                model.decoder.weight.zero_();bias=model.decoder.bias.reshape(64,54);bias.zero_()
                bias[:,:18].copy_(((code-model.output_mean)/model.output_scale)[0])
            raw=inverse(model.decode(torch.zeros(1,32))[0])
            torch.testing.assert_close(raw,torch.from_numpy(rows['values'].copy()).double(),rtol=1e-5,atol=1e-6)

    def test_invalid_market_targets_have_no_gradient(self):
        p=torch.zeros(2,64,18,requires_grad=True);time=torch.zeros(2,64,9,requires_grad=True);vl=torch.zeros(2,64,27,requires_grad=True)
        target=torch.zeros(2,64,27);valid=torch.ones_like(target,dtype=torch.bool);valid[:,:,6:18]=False
        loss,_=objective((p,time,vl),target,valid,torch.ones(2,64,dtype=torch.bool),torch.zeros(27),torch.ones(27));loss.backward()
        self.assertEqual(float(p.grad[:,:,6:].abs().sum()),0.)


if __name__=='__main__':unittest.main()
