"""Reproduce the full failed metric and retain bounded exact outlier windows."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,hashlib,json,time
from pathlib import Path
import torch
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher,prepare
from ..phase1_v12.step import InferenceReplay
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import load_checkpoint,lease
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json


def load_model(path,cal):
    saved=torch.load(path,map_location='cpu',weights_only=False)
    if path.with_suffix('.proof.json').exists():saved=load_checkpoint(path,saved['contract'])
    elif file_hash(path)!=json.loads((path.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Changed checkpoint')
    m=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda().eval()
    m.load_state_dict(saved['model']);return m


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=1800);a=p.parse_args()
    a.device='cuda';a.resume=None;a.batch_size=a.validation_batch_size=8192;a.workers=a.validation_workers=16
    a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    if not 1<=a.seconds<=3000:raise ValueError('Bounded audit required')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');cp=root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json'
    cal=json.loads(cp.read_text());manifest=load_split(root/'phase1-v6-cohort-balanced-v4/split.json')
    if manifest['manifest_hash']!=cal['split_hash']:raise ValueError('Changed split')
    teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda');model=load_model(a.checkpoint,cal)
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    write_json(a.output/'manifest.json',dict(source=a.source_revision,checkpoint_sha=file_hash(a.checkpoint),parent_sha=file_hash(a.parent),split_hash=cal['split_hash'],calibration_sha=file_hash(cp),batch=8192,workers=16,mask_seed=917,scope='Full validation forensic scan; no fitting on retained cases'))
    generator=torch.Generator(device='cuda').manual_seed(917);replay=None;source=device=None
    totals=torch.zeros(2,device='cuda',dtype=torch.float64);windows=0;cases=[];began=time.perf_counter()
    with lease(a.output):
        try:
            source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda')
            while True:
                if (a.output/'STOP').exists() or time.perf_counter()-began>a.seconds:raise InterruptedError('Audit stopped; no complete result')
                try:b=device.pop()
                except StopIteration:break
                pos=positions(len(b.grid),'cuda',16,generator)
                if len(b.grid)==8192:
                    if replay is None:replay=InferenceReplay(model,teacher,b,pos)
                    z,meta,valid,x,v,pred,_=replay(b,pos)
                else:
                    z,meta,valid,x,v=prepare(teacher,b);pred=model(z,meta,pos)
                logs=teacher.decode(model.denormalize(pred))['logs'][...,10].double()
                estimate=logs.expm1();target=b.grid[...,10].double()
                hidden=~torch.zeros_like(valid).scatter(1,pos,True)
                mask=hidden&v[...,10]&(b.frames[:,None]==100000)
                error=torch.where(mask,(estimate-target).abs(),0);row_error=error.sum(1)
                totals+=torch.stack((error.sum(),mask.sum()))
                values,ids=row_error.topk(min(8,len(row_error)))
                threshold=cases[-1]['error_sum'] if len(cases)==32 else 0
                if float(values[0])>threshold:
                    for value,row in zip(values.cpu().tolist(),ids.cpu().tolist()):
                        if value<=threshold:continue
                        bar=int(error[row].argmax());raw=b.grid[row].cpu();ends=b.slots[row].cpu();validity=b.valid[row].cpu()
                        digest=hashlib.sha256(raw.numpy().tobytes()+ends.numpy().tobytes()+validity.numpy().tobytes()).hexdigest()
                        cases.append(dict(stream_window=windows+row,window_sha=digest,error_sum=value,bar=bar,target=float(target[row,bar]),prediction=float(estimate[row,bar]),log_prediction=float(logs[row,bar]),
                            raw=raw,ends=ends,valid=validity,frame=b.frames[row].cpu(),positions=pos[row].cpu(),latent=z[row].cpu(),metadata=meta[row].cpu()))
                    cases=sorted(cases,key=lambda c:c['error_sum'],reverse=True)[:32]
                windows+=len(b.grid);device.advance()
                if windows//8192%100==0:write_json(a.output/'progress.json',dict(stage='forensic full validation',windows=windows,total=manifest['counts']['validation'],seconds=time.perf_counter()-began,speed=windows/(time.perf_counter()-began)))
            if windows!=manifest['counts']['validation']:raise ValueError('Incomplete validation scan')
            torch.save(cases,a.output/'cases.pt')
            summaries=[{k:v for k,v in c.items() if not isinstance(v,torch.Tensor)} for c in cases]
            result=dict(windows=windows,full_population=True,seconds=time.perf_counter()-began,absolute_error_sum=float(totals[0]),count=int(totals[1]),mae=float(totals[0]/totals[1]),top32_window_error_fraction=sum(c['error_sum'] for c in cases)/float(totals[0]),cases=summaries)
            z=torch.stack([c['latent'] for c in cases]).cuda();meta=torch.stack([c['metadata'] for c in cases]).cuda();pos=torch.stack([c['positions'] for c in cases]).cuda()
            parent=load_model(a.parent,cal);diagnostic={}
            for name,m in [('stopped',model),('parent',parent)]:
                diagnostic[name]={}
                for scaled in (True,False):
                    m.encoder.count_scaled=scaled
                    for visible in (16,64):
                        pp=pos if visible==16 else positions(len(z),'cuda',64)
                        out=teacher.decode(m.denormalize(m(z,meta,pp)))['logs'][...,10].double().expm1()
                        diagnostic[name][f'visible{visible}_count_scaled{scaled}']=[float(out[i,c['bar']]) for i,c in enumerate(cases)]
                m.encoder.count_scaled=True
            result['diagnostic_only_no_retraining']=diagnostic;write_json(a.output/'result.json',result)
            print({k:v for k,v in result.items() if k not in ('cases','diagnostic_only_no_retraining')},flush=True)
        finally:
            if device:device.close()
            if source:close_loader(source)


if __name__=='__main__':main()
