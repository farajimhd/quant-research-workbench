"""Native two-step optimizer/RNG/traversal resume audit after the campaign exits."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,time
from pathlib import Path
import torch
from .data import load
from .model import restore
from .train import optimizer,sample,generators
from .step import qualified,rate
from ..v5.shards.phase1_contract import write_json,file_hash
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--revision',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.device='cuda';a.source_revision=a.revision;a.resume=False;preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    start=time.time()
    with lease(a.output):
        data,_,_,provenance=load(Path('D:/TradingML/runtimes/bar_gpt'));saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
        if saved['contract']['data_hash']!=provenance['data_hash']:raise ValueError('Data mismatch')
        def execute(state,steps):
            model=restore(state).cuda();model.configure(state['mode'],state['unfrozen']);opt=optimizer(model,state['peaks']);opt.load_state_dict(state['optimizer'])
            g=generators(state['seed'])
            for gg,ss in zip(g,state['rng']):gg.set_state(ss)
            torch.set_rng_state(state['torch_rng']);torch.cuda.set_rng_state_all(state['cuda_rng']);graphs={};values=[]
            step=state['step'];total=state['contract']['windows']//state['batch']
            for _ in range(steps):
                b=sample(data,state['batch'],step,g);key=b[-2].shape[1]
                if key not in graphs:graphs[key]=qualified(model,b)
                for group in opt.param_groups:
                    offset=total//4 if state['mode']=='gradual' and group['name']!='fusion' else 0
                    group['lr']=rate(max(0,step-offset),total-offset,group['peak'])
                value,_,_=graphs[key](b);opt.step();values.append(float(value));step+=1
            updated=dict(state,model={k:v.detach().cpu().clone() for k,v in model.state_dict().items()},optimizer=opt.state_dict(),step=step,
                rng=[gg.get_state() for gg in g],torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all())
            # Serialization is intentional: do not compare aliased optimizer state.
            temporary=a.output/'audit-state.pt';torch.save(updated,temporary);updated=torch.load(temporary,map_location='cpu',weights_only=False)
            graphs.clear();del model,opt;gc.collect();torch.cuda.empty_cache();return updated,values
        uninterrupted,values=execute(saved,2);middle,first=execute(saved,1);resumed,second=execute(middle,1)
        maximum=0.
        for k,v in uninterrupted['model'].items():
            torch.testing.assert_close(v,resumed['model'][k],rtol=0,atol=0)
            if v.is_floating_point():maximum=max(maximum,float((v-resumed['model'][k]).abs().max()))
        for left,right in zip(uninterrupted['rng'],resumed['rng']):torch.testing.assert_close(left,right,rtol=0,atol=0)
        for key,left in uninterrupted['optimizer']['state'].items():
            for name,value in left.items():
                right=resumed['optimizer']['state'][key][name]
                if torch.is_tensor(value):torch.testing.assert_close(value,right,rtol=0,atol=0)
                elif value!=right:raise ValueError('Optimizer resume differs')
        torch.testing.assert_close(uninterrupted['torch_rng'],resumed['torch_rng'],rtol=0,atol=0)
        for left,right in zip(uninterrupted['cuda_rng'],resumed['cuda_rng']):torch.testing.assert_close(left,right,rtol=0,atol=0)
        if values!=first+second:raise ValueError('Resume loss differs')
        write_json(a.output/'result.json',dict(checkpoint=str(a.checkpoint),sha=file_hash(a.checkpoint),source=a.revision,model_max_error=maximum,
            losses=values,step=uninterrupted['step'],optimizer_exact=True,traversal_rng_exact=True,global_rng_exact=True,seconds=time.time()-start))
        (a.output/'audit-state.pt').unlink()
        print(json.dumps(dict(model_max_error=maximum,losses=values,seconds=time.time()-start)))


if __name__=='__main__':main()
