import unittest
import numpy as np
import torch
from .dataset import SAMPLE
from .data import cache,calibrate,anchor
from .model import Config,JointModel,structured,objective,EmbeddingForecaster
from ..phase1_v8.test_contract import sample
from ..phase1_v8.model import coordinates


def examples(n=4):
    result=np.zeros(n,dtype=SAMPLE)
    for i in range(n):
        context=sample(16);context['values']*=1+i*.1
        # Keep safe dynamic range; source-valid quote geometry unchanged.
        context['values']=np.tile(sample(1)['values'],(16,1,1))*(1+i*.1)
        result['context'][i]=context;result['origin_us'][i]=context['end_us'].max()
        for key in ('values','valid','end_us'):result['future'][i][key]=context[7][key]
        result['future'][i]['end_us']+=64*1000000
        result['future'][i]['values']*=1.01
    return result


class ModelContract(unittest.TestCase):
    def test_anchor_fit_uses_only_training_embeddings_and_freezes_encoder(self):
        from .anchor_fit import features,fit,evaluate
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',linear_context=True,last_context=True,context_embedding=32,activity_basis=True)
        m=JointModel(c,**calibrate(d)).eval();before={k:v.clone() for k,v in m.encoder.state_dict().items()}
        extracted=features(m,{'x':d['x'],'valid':d['valid']},'cpu')
        initial=evaluate(m,extracted);fit(m,extracted);after=evaluate(m,extracted)
        self.assertLess(after['price_relative_mean'],initial['price_relative_mean'])
        for k,v in m.encoder.state_dict().items():torch.testing.assert_close(v,before[k],rtol=0,atol=0)
        with torch.no_grad():
            for p in m.encoder.parameters():p.zero_()
            a=m(d['x'],d['valid'],d['ages'])['forecast'];b=m(d['x']+1,d['valid'],d['ages'])['forecast']
            torch.testing.assert_close(a,b,rtol=0,atol=0)

    def test_activity_basis_is_visible_only_and_learned(self):
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',linear_context=True,last_context=True,context_embedding=32,activity_basis=True)
        m=JointModel(c,**calibrate(d)).eval();self.assertEqual(m.encoder.context.in_features,102)
        v=d['valid'].clone();v[:,:,16:]=False;x=d['x'].clone();x[:,:,16:]+=100
        torch.testing.assert_close(m.encode_windows(x,v),m.encode_windows(d['x'],v),rtol=0,atol=0)
        o=m(d['x'],d['valid'],d['ages']);loss,_=objective(m,o,d);loss.backward()
        self.assertTrue(torch.isfinite(m.encoder.context.weight.grad).all())
        with torch.no_grad():
            for p in m.encoder.parameters():p.zero_()
            self.assertEqual(float(m.encode_windows(d['x'],d['valid']).abs().sum()),0)

    def test_activity_coordinates_can_reconstruct_constant_levels(self):
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',linear_context=True,last_context=True,context_embedding=32,activity_basis=True)
        m=JointModel(c,**calibrate(d)).eval();levels=torch.tensor([0.,1.,20.,40.])
        x=d['x'].clone();v=d['valid'].clone();x[...,4]=levels[:,None,None];v[...,4]=True
        with torch.no_grad():
            for p in m.encoder.parameters():p.zero_()
            m.encoder.context.weight[0,90]=4;m.encoder.context_export.weight[0,0]=1
            m.reconstruction.weight.zero_();m.reconstruction.bias.zero_()
            rows=torch.arange(64)*54+4
            m.reconstruction.weight[rows,32]=1/m.reconstruction_scale[4]
            m.reconstruction.bias[rows]=-m.reconstruction_mean[4]/m.reconstruction_scale[4]
            predicted=m.reconstruct(m.encode_windows(x,v))[0][...,4]
        torch.testing.assert_close(predicted,levels[:,None,None].expand_as(predicted),rtol=1e-6,atol=1e-5)

    def test_horizon_queries_are_fixed_and_export_exactly(self):
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',horizon_queries=True)
        m=JointModel(c,**calibrate(d)).eval();o=m(d['x'],d['valid'],d['ages'])
        self.assertEqual(o['forecast'].shape,(4,64,18));self.assertEqual(o['joint'].shape,(4,64))
        self.assertEqual(m.query.shape,(1,64,64));self.assertEqual(m.forecast.out_features,18)
        f=EmbeddingForecaster.from_model(m).eval()
        torch.testing.assert_close(f(o['windows'],d['ages'])['forecast'],o['forecast'],rtol=0,atol=0)
        changed={**d,'target':d['target']+100,'target_valid':~d['target_valid']}
        torch.testing.assert_close(m(changed['x'],changed['valid'],changed['ages'])['forecast'],o['forecast'],rtol=0,atol=0)
        loss,_=objective(m,o,d);loss.backward();self.assertTrue(torch.isfinite(m.query.grad).all())

    def test_separate_learned_context_export(self):
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',readout='flatten',linear_context=True,last_context=True,context_embedding=32)
        m=JointModel(c,**calibrate(d)).eval();out=m(d['x'],d['valid'],d['ages'])
        self.assertEqual(out['windows'].shape,(4,16,64))
        loss,_=objective(m,out,d);loss.backward()
        self.assertGreater(float(m.encoder.context_export.weight.grad.abs().sum()),0)
        with torch.no_grad():
            for p in m.encoder.parameters():p.zero_()
            z=m.encode_windows(d['x'],d['valid']);self.assertEqual(float(z.abs().sum()),0)
            a=m(d['x'],d['valid'],d['ages'])['forecast'];b=m(d['x']+1,d['valid'],d['ages'])['forecast']
            torch.testing.assert_close(a,b,rtol=0,atol=0)

    def test_reconstruction_pretext_has_no_future_gradient(self):
        from .train import loss_for
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned')
        model=JointModel(c,**calibrate(d)).eval()
        loss,_=loss_for(model,d,1.,forecast_weight=0.)
        changed={**d,'target':d['target']+10,'target_valid':~d['target_valid']}
        other,_=loss_for(model,changed,1.,forecast_weight=0.)
        torch.testing.assert_close(loss,other,rtol=0,atol=0)
        loss.backward()
        self.assertGreater(float(model.encoder.export.weight.grad.abs().sum()),0)
        self.assertIsNone(model.forecast.weight.grad)

    def test_forecast_only_embeddings_with_learned_anchor(self):
        from unittest.mock import patch
        d=cache(examples());c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',anchor_mode='learned',last_context=True,linear_context=True)
        model=JointModel(c,**calibrate(d)).eval()
        with torch.no_grad(),patch('research.bar_gpt.joint_v9.model.anchor',side_effect=AssertionError('Raw anchor forbidden')):
            out=model(d['x'],d['valid'],d['ages'])
            decoded=model.forecast_from_embeddings(out['windows'],d['ages'])
            torch.testing.assert_close(out['forecast'],decoded['forecast'],rtol=0,atol=0)
            standalone=EmbeddingForecaster.from_model(model).eval()
            torch.testing.assert_close(standalone(out['windows'],d['ages'])['forecast'],decoded['forecast'],rtol=0,atol=0)
            self.assertFalse(hasattr(standalone,'encoder'));self.assertFalse(hasattr(standalone,'reconstruction'))
            for value in model.encoder.parameters():value.zero_()
            a=model(d['x'],d['valid'],d['ages'])['forecast']
            b=model(d['x']+100,d['valid'],d['ages'])['forecast']
            torch.testing.assert_close(a,b,rtol=0,atol=0)
            with self.assertRaises(ValueError):model.forecast_from_embeddings(out['windows'],d['ages'],torch.zeros(4,18))

    def test_flat_patch_readout_and_learned_linear_scale_path(self):
        d=cache(examples())
        for readout,linear in [('flatten',False),('cls',True),('flatten',True)]:
            c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',readout=readout,linear_context=linear)
            model=JointModel(c,**calibrate(d));o=model(d['x'],d['valid'],d['ages']);loss,_=objective(model,o,d,.1);loss.backward()
            self.assertEqual(o['windows'].shape,(4,16,64));self.assertTrue(torch.isfinite(model.encoder.export.weight.grad).all())
            with torch.no_grad():
                for value in model.encoder.parameters():value.zero_()
                z=model.encode_windows(d['x'],d['valid']);torch.testing.assert_close(z,torch.zeros_like(z),rtol=0,atol=0)
                p=model.reconstruct(z)[0];torch.testing.assert_close(p,p[:1,:1].expand_as(p),rtol=0,atol=0)

    def test_auxiliary_future_loss_and_masked_reconstruction_gradients(self):
        from .train import loss_for
        d=cache(examples());model=JointModel(Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',summary_weight=.2,price_huber_beta=.05),**calibrate(d))
        loss,parts=loss_for(model,d,.1,1.);loss.backward()
        self.assertGreater(float(parts['future_summary'].detach()),0)
        self.assertTrue(torch.isfinite(model.future_summary.weight.grad).all())
        self.assertTrue(torch.isfinite(model.encoder.export.weight.grad).all())
        model.eval()
        with torch.no_grad():
            a=model(d['x'],d['valid'],d['ages'])['future_summary']
            d['target']*=10;d['target_valid'][:]=False
            b=model(d['x'],d['valid'],d['ages'])['future_summary']
        torch.testing.assert_close(a,b,rtol=0,atol=0)

    def test_masked_encoder_has_no_hidden_reference(self):
        d=cache(examples());model=JointModel(Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32',last_context=True),**calibrate(d)).eval()
        visible=d['valid'].clone();visible[:,:,16:]=False
        changed=d['x'].clone();changed[:,:,16:]+=12345
        with torch.no_grad():
            a=model.encode_windows(d['x'],visible);b=model.encode_windows(changed,visible)
        torch.testing.assert_close(a,b,rtol=0,atol=0)

    def test_detached_reconstruction_probe_does_not_train_encoder(self):
        data=cache(examples());model=JointModel(Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32'),**calibrate(data))
        z=model.encode_windows(data['x'],data['valid']);loss=model.reconstruct(z.detach())[0].sum();loss.backward()
        self.assertTrue(all(v.grad is None for v in model.encoder.parameters()))
        self.assertTrue(any(v.grad is not None for v in model.reconstruction.parameters()))

    def test_dropout_rng_optimizer_resume_next_update(self):
        import tempfile
        from pathlib import Path
        from .train import loss_for
        torch.manual_seed(71);data=cache(examples());parameters=calibrate(data)
        c=Config(width=32,embedding=64,layers=1,global_layers=1,dropout=.1,precision='fp32')
        model=JointModel(c,**parameters);opt=torch.optim.AdamW(model.parameters(),lr=.001)
        scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,10);gen=torch.Generator().manual_seed(5)
        def update(m,o,s,g):
            ids=torch.randint(4,(4,),generator=g);b={k:v[ids] for k,v in data.items()}
            o.zero_grad(set_to_none=True);loss,_=loss_for(m,b,.1);loss.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),1.);o.step();s.step()
            return ids
        update(model,opt,scheduler,gen)
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            path=Path(folder)/'state.pt';torch.save(dict(model=model.state_dict(),optimizer=opt.state_dict(),scheduler=scheduler.state_dict(),rng=torch.get_rng_state(),generator=gen.get_state()),path)
            ids=update(model,opt,scheduler,gen);state=torch.load(path,weights_only=False)
            restored=JointModel(c,**parameters);restored.load_state_dict(state['model']);ro=torch.optim.AdamW(restored.parameters(),lr=.001)
            rs=torch.optim.lr_scheduler.CosineAnnealingLR(ro,10);ro.load_state_dict(state['optimizer']);rs.load_state_dict(state['scheduler'])
            rg=torch.Generator();rg.set_state(state['generator']);torch.set_rng_state(state['rng'])
            torch.testing.assert_close(update(restored,ro,rs,rg),ids,rtol=0,atol=0)
            for key,v in model.state_dict().items():torch.testing.assert_close(restored.state_dict()[key],v,rtol=0,atol=0)

    def test_architectures_shapes_gradients_and_decoder_only_latent(self):
        torch.set_num_threads(2);data=cache(examples());params=calibrate(data)
        for arch in ('mlp','conv','patch','bar','variable'):
            model=JointModel(Config(architecture=arch,width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32'),**params)
            out=model(data['x'],data['valid'],data['ages']);self.assertEqual(out['forecast'].shape,(4,64,18));self.assertEqual(out['windows'].shape,(4,16,64))
            loss,_=objective(model,out,data,.1);loss.backward()
            self.assertTrue(all(torch.isfinite(v.grad).all() for v in model.parameters() if v.grad is not None))
            with torch.no_grad():
                for value in model.encoder.parameters():value.zero_()
                z=model.encode_windows(data['x'],data['valid']);torch.testing.assert_close(z,torch.zeros_like(z),rtol=0,atol=0)
                p=model.reconstruct(z)[0];torch.testing.assert_close(p,p[:1,:1].expand_as(p),rtol=0,atol=0)

    def test_no_future_values_masks_or_timestamps_affect_inputs_or_prediction(self):
        rows=examples();data=cache(rows);model=JointModel(Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32'),**calibrate(data)).eval()
        changed=rows.copy();changed['future']['values']*=10;changed['future']['end_us']+=99999999;changed['future']['valid']=0
        other=cache(changed)
        with torch.no_grad():
            a=model(data['x'],data['valid'],data['ages']);b=model(other['x'],other['valid'],other['ages'])
        for key in ('x','valid','ages'):torch.testing.assert_close(data[key],other[key],rtol=0,atol=0)
        torch.testing.assert_close(a['forecast'],b['forecast'],rtol=0,atol=0)

    def test_geometry_roundtrip_and_history_anchor(self):
        data=cache(examples());code,mask=coordinates(data['target'],data['target_valid'],'joint')
        actual=structured(code);complete=data['target_valid'].all(-1)
        torch.testing.assert_close(actual[complete],data['target'][complete],atol=1e-6,rtol=1e-5)
        a=anchor(data['x'],data['valid']);self.assertTrue(torch.isfinite(a).all())


if __name__=='__main__':unittest.main()
