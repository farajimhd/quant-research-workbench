"""CPU-only qualification of the exact2B expansion on real immutable windows."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .model import build,EXTREME_SPEC
from ..phase1_v23.model import restore
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.model import positions
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    started=time.time();torch.set_num_threads(4);torch.manual_seed(17)
    root=Path('D:/TradingML/runtimes/bar_gpt')
    cp=root/'phase1-v23-large-20261122/e03-w384-d6-s559/checkpoint_last.pt'
    if file_hash(cp)!=json.loads((cp.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Parent changed')
    saved=torch.load(cp,map_location='cpu',weights_only=False)
    parent={k:saved[k] for k in ('model','contract','parent_template','architecture')};del saved
    old=restore(parent).eval();new=build(parent,**EXTREME_SPEC).eval()
    rows=np.load(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r')
    x,v,_=gpu_inputs(prepare_raw(rows[:2],True));tests=[]
    with torch.no_grad():
        for count in (16,64):
            pos=positions(len(x),'cpu',count);expected=old(x,v,pos)[2]['code'];actual=new(x,v,pos)[2]['code']
            torch.testing.assert_close(actual,expected,rtol=1e-4,atol=1e-5)
            tests.append(dict(visible=count,max_absolute_error=float((actual-expected).abs().max())))
    result=dict(parameters=sum(p.numel() for p in new.parameters()),decoder_parameters=sum(p.numel() for p in new.decoder.parameters()),architecture=EXTREME_SPEC,parity=tests,seconds=time.time()-started)
    assert 1.9e9<result['parameters']<2.1e9 and result['decoder_parameters']==594
    write_json(a.output,result);print(json.dumps(result))


if __name__=='__main__':main()
