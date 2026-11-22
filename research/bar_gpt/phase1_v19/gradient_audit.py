"""Training-only component-gradient audit; no optimizer or checkpoint mutation."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json
from pathlib import Path
import torch
from .model import Autoencoder,architecture
from .objectives import objective,calibrate
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher
from ..phase1_v15.experiment import cache
from ..phase1_v17.sampling import FrameSampler
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json,digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New identity required')
    a.output.mkdir();torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');cp=root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json';cal=json.loads(cp.read_text())
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);receipt=json.loads((a.checkpoint.parent/'result.json').read_text())
    if file_hash(a.checkpoint)!=receipt['checkpoint_sha']:raise ValueError('Parent mismatch')
    with lease(a.output):
        teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
        base=root/'phase1-v8-learned-mae-20261117/dataset/train.npy';extra=root/'phase1-v19-training-pool-20261121/train.npy'
        manifest=json.loads((extra.parent/'manifest.json').read_text())
        if digest({k:v for k,v in manifest.items() if k!='hash'})!=manifest['hash'] or file_hash(extra)!=manifest['files']['train.npy']:raise ValueError('Supplement changed')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(base,teacher),cache(extra,teacher)))
        scale,_=calibrate(train[6],train[4],train[5]);sampler=FrameSampler(train[5]);g=torch.Generator(device='cuda').manual_seed(559)
        model=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],architecture(saved['contract'])).cuda();model.load_state_dict(saved['model']);model.eval();results=[]
        for balanced in (False,True):
            ids=sampler.sample(1024,g) if balanced else torch.randint(len(train[0]),(1024,),device='cuda',generator=g)
            z,meta,valid,_,v,frames,raw=[x[ids] for x in train]
            for count in (16,64):
                pos=positions(len(z),'cuda',count,g);pred=model(z,meta,pos)
                total,parts=objective(model,teacher,pred,z,valid,pos,raw,v,'relative_all',frames,scale)
                terms={'body':.5*parts['body'],'activity_level':parts['activity_log_mse'],'activity_change':parts['activity_delta_mse'],'activity_tail':.01*parts['activity_tail'],'activity_relative':.1*parts['activity_relative'],'price_relative':parts['price_relative']}
                terms['latent_and_price_standardized']=total-sum(terms.values())
                grads={k:torch.cat([x.flatten() for x in torch.autograd.grad(value,(model.encoder.readout.bias,model.decoder.bias),retain_graph=True)]) for k,value in terms.items()}
                price=grads['price_relative']+grads['latent_and_price_standardized']
                results.append(dict(balanced=balanced,visible=count,total=float(total.detach()),frame_counts={str(int(f)):int((frames==f).sum()) for f in frames.unique()},terms={k:dict(loss=float(value.detach()),gradient_norm=float(grads[k].norm()),cosine_with_price=float(torch.nn.functional.cosine_similarity(grads[k],price,dim=0))) for k,value in terms.items()}))
        write_json(a.output/'result.json',dict(source=a.source_revision,parent_sha=file_hash(a.checkpoint),base_sha=file_hash(base),supplement_hash=manifest['hash'],results=results,scope='Training-only1024-window draws; gradients of learned readout/decoder biases, not the entire parameter vector; no optimizer updates'))
        print(json.dumps(results,indent=2))


if __name__=='__main__':main()
