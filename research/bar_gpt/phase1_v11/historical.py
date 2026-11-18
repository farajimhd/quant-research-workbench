"""Pinned V10 control on identical windows/masks; training budgets differ."""
import json
import numpy as np
import torch
from ..phase1_v10.model import Autoencoder,Config,inverse
from ..phase1_v10.data import read_calibration
from ..phase1_v8.data import batch
from ..phase1_v7.metrics import evaluate,require_structured
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import file_hash
from .probes import compare


@torch.no_grad()
def compare_v10(path,calibration_path,data,rows,status,with_probes=False):
    saved=torch.load(path,map_location='cpu',weights_only=False);saved=load_checkpoint(path,saved['contract']);calibration=read_calibration(calibration_path)
    if saved['contract']['calibration']!=calibration['hash']:raise ValueError('Historical calibration differs')
    model=Autoencoder(Config(**saved['contract']['architecture']),calibration['parameters']).cuda().eval()
    model.load_state_dict(saved['model']);result=dict(checkpoint=str(path),checkpoint_sha256=file_hash(path),training_step=saved['step'],contract=saved['contract'],
        scope='Historical full-data V10 epoch4 best; much larger training exposure than the V11 subset experiments. Identical frozen dev windows and random16 masks; not an architecture-only ablation.')
    for mode in ('masked','all_visible'):
        predictions=[];masks=[];generator=torch.Generator(device='cuda').manual_seed(314159)
        for start in range(0,len(rows['development']),256):
            ids=torch.arange(start,min(start+256,len(rows['development'])),device='cuda')
            x,v,pos,t,tv,h=batch(data['development'],ids,16,generator,full=mode=='all_visible')
            out=model(x,v,pos);predictions.append(inverse(out[0]).cpu().numpy());masks.append(h.cpu().numpy()[...,None])
            if start%2048==0:status(stage='historical V10 '+mode,done=start+len(ids),total=len(rows['development']))
        result[mode]=evaluate(np.concatenate(predictions),rows['development'],np.concatenate(masks));require_structured(result[mode],paired=True)
    if with_probes:
        torch.manual_seed(saved['contract']['seed']);untrained=Autoencoder(model.config,calibration['parameters']).cuda()
        result['probes']=compare(model,untrained,data,rows)
    return result
