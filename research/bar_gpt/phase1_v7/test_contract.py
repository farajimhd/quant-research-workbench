"""CPU integrity tests for the real encoder/decoder and training batch path."""
import unittest
import tempfile
from pathlib import Path
import torch
import numpy as np
from .model import Config, Autoencoder, Transform, objective, PRICES, ACTIVITY
from .train import batch, save_checkpoint
from .export import EncoderOnly



class Contracts(unittest.TestCase):
    def test_throughput_resume_requires_completed_pass(self):
        import json
        from unittest.mock import patch
        from .full_engineering import resume
        from ..v5.shards.phase1_contract import digest,file_hash
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as directory:
            root=Path(directory);checkpoint=root/'checkpoint.pt';checkpoint.write_bytes(b'parent')
            previous=dict(source_revision='old',preparation='raw_gpu',batch_size=4096,learning_rate=.0003)
            current=dict(previous,source_revision='new',batch_size=8192,validation_graph=True)
            checkpoint.with_suffix('.proof.json').write_text(json.dumps(dict(contract=digest(previous))))
            receipt=dict(transition='completed-pass-throughput-v1',previous_contract=previous,new_source_revision='new',
                checkpoint_sha256=file_hash(checkpoint),evidence={},validation_parity=True,user_authorized_batch_change=True,training_population=100)
            receipt['hash']=digest(receipt);path=root/'receipt.json';path.write_text(json.dumps(receipt))
            with patch('research.bar_gpt.phase1_v7.full_engineering.load_checkpoint',return_value=dict(phase='validation',epoch_done=100,step=9)) as load:
                self.assertEqual(resume(checkpoint,current,path)[0]['step'],9)
                with self.assertRaisesRegex(ValueError,'scientific'):resume(checkpoint,dict(current,learning_rate=.001),path)
                load.return_value['phase']='train'
                with self.assertRaisesRegex(ValueError,'completed training pass'):resume(checkpoint,current,path)
                load.return_value.update(phase='validation',epoch_done=99)
                with self.assertRaisesRegex(ValueError,'completed training pass'):resume(checkpoint,current,path)

    def test_panel_uses_validation_rate_and_hides_stale_training_speed(self):
        import io,time
        from rich.console import Console
        from .full_panel import render
        state=dict(state='running',stage='full validation',updated=time.time(),windows_per_second=999999,
            validation_windows_per_second=123456,validation_eta_seconds=60,epoch=1,epoch_done=100,epoch_total=100,
            validation_done=50,validation_total=100)
        for width,height in ((80,24),(120,35)):
            out=io.StringIO();Console(file=out,width=width,height=height).print(render(state,width,height))
            text=out.getvalue();self.assertNotIn('999',text);self.assertIn('Validation',text);self.assertIn('phase ETA',text)

    def test_engineering_resume_rejects_scientific_changes(self):
        import json
        from unittest.mock import patch
        from .full_engineering import resume
        from ..v5.shards.phase1_contract import digest,file_hash
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);checkpoint=root/'checkpoint.pt';checkpoint.write_bytes(b'parent')
            previous=dict(source_revision='old',preparation='gpu',batch_size=4096)
            current=dict(previous,source_revision='new',preparation='raw_gpu')
            checkpoint.with_suffix('.proof.json').write_text(json.dumps(dict(contract=digest(previous))))
            evidence={}
            for name,value in dict(precision=dict(bf16_accepted=True,preparation_prediction_parity=True),
                engineering=dict(graph_eager_update_parity=True,checkpoint_resume_update_parity=True,ratio=.01),
                stream=dict(windows_per_second=200000)).items():
                path=root/(name+'.json');path.write_text(json.dumps(value));evidence[name]=dict(path=str(path),sha256=file_hash(path))
            receipt=dict(previous_contract=previous,new_source_revision='new',checkpoint_sha256=file_hash(checkpoint),evidence=evidence)
            receipt['hash']=digest(receipt);path=root/'receipt.json';path.write_text(json.dumps(receipt))
            with patch('research.bar_gpt.phase1_v7.full_engineering.load_checkpoint',return_value={'step':9}):
                state,_=resume(checkpoint,current,path);self.assertEqual(state['step'],9)
                with self.assertRaisesRegex(ValueError,'Scientific'):resume(checkpoint,dict(current,batch_size=8192),path)
                checkpoint.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'parent checkpoint'):resume(checkpoint,current,path)

    def test_full_worker_preparation_and_gradients(self):
        from .full_data import prepare
        from .train import cache
        from ..v5.shards.phase1_contract import SAMPLE_DTYPE
        rows=np.zeros(3,dtype=SAMPLE_DTYPE)
        rows['values']=1;rows['valid']=1;rows['timeframe_us']=1000000
        rows['end_us']=1700000000000000+np.arange(64)*1000000
        rows['values'][:,:,12:16]=1.01
        rows['valid'][0,:4,6:18]=0
        prepared=prepare(rows,True)
        data=cache(rows,torch.zeros(18),torch.ones(18),'cpu')
        model=Autoencoder(Config(variant='geometry_joint',width=64,embedding=64,layers=1),torch.zeros(18),torch.ones(18))
        z,p=model(data['values'],data['valid'],torch.arange(64).expand(3,-1))
        other=model.encode_prepared(prepared.grid,prepared.slots)
        torch.testing.assert_close(z,other,rtol=0,atol=0)
        torch.testing.assert_close(p,model.decode(other),rtol=0,atol=0)
        from .full_step import forward
        compact=prepare(rows,compact=True)
        torch.testing.assert_close(p,forward(model,compact,'fp32'),rtol=0,atol=0)
        raw=prepare(rows,raw_gpu=True)
        torch.testing.assert_close(p,forward(model,raw,'fp32'),rtol=0,atol=0)
        self.assertIs(raw.grid,raw.target)
        self.assertIs(raw.to('cpu').grid,raw.to('cpu').target)
        loss,_=objective(p,prepared.target,prepared.valid,None);loss.backward()
        self.assertTrue(all(torch.isfinite(v.grad).all() for v in model.parameters()))

    def test_joint_coordinate_inverse_oracle_diagnostic(self):
        from .model import quote_geometry
        model=Autoencoder(Config(variant='geometry_joint',width=32,embedding=64,layers=1),torch.zeros(18),torch.ones(18))
        level=torch.linspace(-16,12,64).exp()
        raw=torch.zeros(1,64,18)
        for base in (0,6,12):
            scale=1.001 if base==12 else 1.
            raw[0,:,base:base+4]=level[:,None]*torch.tensor([1.,1.05,.95,1.01])*scale
            raw[0,:,base+4:base+6]=torch.linspace(0,20,64).expm1()[:,None]
        valid=torch.ones_like(raw,dtype=torch.bool);logs=Transform.logs(raw,valid).float()
        x=torch.cat((logs,torch.zeros(1,64,9)),-1)
        z=model.encode(x,torch.ones_like(x,dtype=torch.bool),torch.arange(64)[None])
        code=torch.zeros_like(logs)
        for base in (0,6,12):
            o,h,l,c=logs[...,base:base+4].unbind(-1)
            code[...,base]=(o-z[:,None,base])/.01
            code[...,base+3]=(c-o)/.01
            code[...,base+1]=((h-torch.maximum(o,c)).sqrt()-.01)/.1
            code[...,base+2]=((torch.minimum(o,c)-l).sqrt()-.01)/.1
            for j in (base+4,base+5):code[...,j]=logs[...,j].sqrt()-z[:,None,j]
        pair,_=quote_geometry(logs,valid)
        code[...,12:16]=(pair-z[:,None,21:25])/.01
        # Explicit oracle-coordinate diagnostic, not trained reconstruction evidence.
        with torch.no_grad():
            model.decoder.weight.zero_();model.decoder.bias.copy_(code.flatten())
        recovered=model.transform.inverse(model.decode(z))
        torch.testing.assert_close(recovered,raw.double(),rtol=1e-4,atol=1e-5)

    def test_control_reporting_is_separate_from_model_acceptance(self):
        from .metrics import evaluate,require_structured
        rows=np.zeros(1,dtype=[('values','f4',(64,18)),('valid','u1',(64,18)),('timeframe_us','i8')])
        rows['values']=1;rows['valid']=1;rows['timeframe_us']=100000
        control=rows['values'].astype(np.float64);control[...,1]=.5
        metrics=evaluate(control,rows)
        self.assertEqual(metrics['frames']['100000']['structure']['trade']['prediction'],64)
        with self.assertRaises(FloatingPointError):require_structured(metrics)

    def test_inverse_difficult_values(self):
        raw=torch.tensor([1e-8,.1,.2,1.,1e4,1e12]).reshape(6,1,1).expand(6,64,18).clone()
        raw[0,:,ACTIVITY]=0
        valid=torch.ones_like(raw,dtype=torch.bool)
        trans=Transform(torch.zeros(18),torch.ones(18))
        out=trans.inverse(Transform.logs(raw,valid).float())
        torch.testing.assert_close(out,raw.double(),rtol=2e-6,atol=1e-10)

    def test_embedding_only_and_hidden_independence(self):
        for variant in ('canonical','geometry','geometry_joint'):
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
            if variant=='geometry_joint':self.assertTrue((raw[...,12:16]>=raw[...,6:10]).all())
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
