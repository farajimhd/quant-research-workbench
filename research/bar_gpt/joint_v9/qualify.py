"""Sealed finalist evaluation and standalone learned-window encoder export."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import torch
from .data import load,cache
from .model import Config,JointModel,WindowEncoder
from .metrics import measure,masked_reconstruction
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--run',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--audit',action='store_true');p.add_argument('--device',default='cuda',choices=('cpu','cuda'));p.add_argument('--data',type=Path)
    a=p.parse_args();a.resume=False;preflight(a);torch.set_num_threads(8);started=time.time()
    cp=a.root/a.run/'best.pt';state=torch.load(cp,map_location=a.device,weights_only=False);identity=state['identity']
    partitions=('audit_2025','audit_2026') if a.audit else ('development','novel_development')
    dataset=a.data or a.root/'dataset';rows,manifest=load(dataset,partitions)
    if manifest['hash']!=identity['dataset_hash']:raise ValueError('Dataset identity mismatch')
    config=Config(**identity['config']);parameters={k:torch.tensor(v) for k,v in identity['parameters'].items()}
    model=JointModel(config,**parameters).to(a.device);model.load_state_dict(state['model']);model.eval()
    report=dict(run=a.run,checkpoint_sha256=file_hash(cp),source_revision=a.source_revision,dataset_hash=manifest['hash'],evaluations={})
    for partition,r in rows.items():
        d={k:v.to(a.device) for k,v in cache(r).items()};identities=json.loads((dataset/(partition+'-identities.json')).read_text())
        begin=time.time();report['evaluations'][partition]=measure(model,d,r,identities)
        report['evaluations'][partition]['masked_reconstruction']=masked_reconstruction(model,d,r)
        report['evaluations'][partition]['seconds']=time.time()-begin
        print(partition,flush=True)
    package=dict(version='joint-v9-window-encoder-1',config=identity['config'],state={k:v.cpu() for k,v in model.encoder.state_dict().items()},
        input_mean=model.input_mean.cpu(),input_scale=model.input_scale.cpu(),checkpoint_sha256=file_hash(cp),identity=identity,
        contract='Input log/log1p 18 market + 9 time features and 27 source-valid flags, shape [N,64,27]. Output learned fixed-size window embedding. No decoder or copied reference fields.')
    standalone=WindowEncoder(config,package['input_mean'],package['input_scale']).to(a.device);standalone.load_state_dict(package['state']);standalone.eval()
    x=d['x'][:2].reshape(-1,64,27);v=d['valid'][:2].reshape(-1,64,27)
    with torch.no_grad():torch.testing.assert_close(standalone(x,v),model.encoder(x,v),rtol=0,atol=0)
    torch.save(package,a.output/'window-encoder.pt');report['encoder_sha256']=file_hash(a.output/'window-encoder.pt')
    report.update(elapsed_seconds=time.time()-started,export_parity='exact on final partition examples',state='complete')
    write_json(a.output/'results.json',report)


if __name__=='__main__':main()
