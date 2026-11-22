"""Standalone exports contain encoder weights and fixed input transforms only."""
import torch
from torch import nn
from pathlib import Path
from ..phase0_v3.model import Autoencoder as Bar
from ..phase0_v2.model import Config as BarConfig
from ..phase1_v19.model import Autoencoder as Window
from ..phase1_v12.model import Config,positions,gather
from .model import JointAutoencoder


def restore(saved):
    s=saved['model'];c=saved['contract']
    bar=Bar(dict(mean=s['bar.mean'],scale=s['bar.scale']),BarConfig(**c['bar_config']),c['input_calibration'])
    temporal=Window(Config(**c['temporal_config']),s['temporal.mean'],s['temporal.scale'],'mask_adapter').encoder
    m=JointAutoencoder(bar,temporal,s['mean'],s['scale'],s['decoder.weight'],s['decoder.bias']);m.load_state_dict(s);return m


class BarEncoder(nn.Module):
    def __init__(self,saved):
        super().__init__();s=saved['bar'];c=saved['contract']
        self.module=Bar(dict(mean=s['mean'],scale=s['scale']),BarConfig(**c['bar_config']),c['input_calibration'])
        del self.module.decoder;self.module.load_state_dict(s)
    def forward(self,values,valid):return self.module.encode(values,valid)


class WindowEncoder(nn.Module):
    def __init__(self,saved):
        super().__init__();self.bar=BarEncoder(saved);s=saved['temporal'];c=saved['contract']
        self.temporal=Window(Config(**c['temporal_config']),s['mean'],s['scale'],'mask_adapter').encoder
        self.temporal.load_state_dict(s)
    def forward(self,values,valid,pos=None):
        if pos is None:pos=positions(len(values),values.device,64)
        x,v=gather(values,pos),gather(valid,pos);z=self.bar(x,v);b=self.bar.module
        timing=torch.where(v[...,18:],(x[...,18:]-b.mean[18:])/b.scale[18:],0)
        return self.temporal(z,torch.cat((timing,v.float()),-1),pos)


def export(model,contract,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if any((directory/n).exists() for n in ('bar_encoder.pt','window_encoder.pt')):raise FileExistsError('Immutable encoder export already exists')
    shared=dict(contract=contract,bar={k:v.detach().cpu() for k,v in model.bar.state_dict().items()})
    torch.save(shared,directory/'bar_encoder.pt')
    torch.save(dict(shared,temporal={k:v.detach().cpu() for k,v in model.temporal.state_dict().items()}),directory/'window_encoder.pt')


def load(path,device='cpu'):
    saved=torch.load(path,map_location='cpu',weights_only=False)
    return (WindowEncoder(saved) if 'temporal' in saved else BarEncoder(saved)).to(device).eval()


def main():
    import argparse,json
    from ..v5.shards.phase1_contract import file_hash,write_json
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text());sha=file_hash(a.checkpoint)
    if sha!=receipt['checkpoint_sha']:raise ValueError('Candidate changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);model=restore(saved);export(model,saved['contract'],a.output)
    write_json(a.output/'manifest.json',dict(checkpoint_sha=sha,export_revision=a.revision,files={n:file_hash(a.output/n) for n in ('bar_encoder.pt','window_encoder.pt')},input='Prepared log prices/log1p activities plus nine time fields and27 source-valid flags; encoder applies its fixed calibration; no decoder in exports'))


if __name__=='__main__':main()
