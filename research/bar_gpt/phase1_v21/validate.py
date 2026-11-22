"""Pinned full-population evaluation of the single-head hierarchical model."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
import wandb
from ..phase1_v20.encoders import restore
from .raw_metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v12.model import positions,gather
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v7.full_data import gpu_inputs
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json
from ..phase2_v2.train import lease
from research.mlops.env import discover_env_files,load_env_files


class Accumulator:
    def __init__(self,device='cuda'):
        self.raw={k:Metrics(device) for k in ('masked','all_visible')}
        self.activity={k:ActivityMetrics(device) for k in self.raw}

    @torch.no_grad()
    def add(self,model,batch,pos):
        x,v,_=gpu_inputs(batch);z,meta=model.features(x,v)
        hidden=~torch.zeros_like(v[...,0]).scatter(1,pos,True)
        for name,p in (('masked',pos),('all_visible',positions(len(x),x.device,64))):
            output=model.decode(model.temporal(gather(z,p),gather(meta,p),p))
            mask=v&hidden[...,None] if name=='masked' else v
            self.raw[name].add(model,output,x,mask,batch)
            self.activity[name].add(output['logs'],batch.grid,mask,batch.frames)

    def buffers(self):
        result=[]
        for metric in self.raw.values():
            result.extend((metric.raw,metric.loss,metric.nonfinite))
            result.extend(metric.moves.tables.values());result.extend(metric.moves.confusions.values())
        for metric in self.activity.values():result.extend((metric.sums,metric.confusion,metric.maxima))
        return result

    def zero(self):
        for tensor in self.buffers():tensor.zero_()
        for metric in self.raw.values():metric.windows=0

    def result(self):
        detail={k:v.result() for k,v in self.raw.items()}
        metrics={name+'/'+k:v for name,r in detail.items() for k,v in scalars(r).items()}
        for name,value in self.activity.items():
            detail['activity_'+name]=value.result()
            metrics.update({'activity/'+name+'/'+k:v for k,v in activity_scalars(detail['activity_'+name]).items()})
        return detail,metrics


class Replay:
    @torch.no_grad()
    def __init__(self,model,accumulator,batch,pos):
        self.accumulator=accumulator;self.batch=batch.map(lambda t:t.clone());self.pos=pos.clone()
        def add():accumulator.add(model,self.batch,self.pos)
        add();expected=[v.clone() for v in accumulator.buffers()];accumulator.zero()
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):add()
        torch.cuda.current_stream().wait_stream(stream);accumulator.zero();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.graph(self.graph,stream=stream):add()
        torch.cuda.current_stream().wait_stream(stream);accumulator.zero();self.graph.replay()
        for actual,wanted in zip(accumulator.buffers(),expected):torch.testing.assert_close(actual,wanted,rtol=1e-6,atol=1e-8)
        accumulator.zero()

    def __call__(self,batch,pos):
        for name in ('grid','slots','valid','frames'):getattr(self.batch,name).copy_(getattr(batch,name))
        self.pos.copy_(pos);self.graph.replay()
        for metric in self.accumulator.raw.values():metric.windows+=len(batch.grid)


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--batch-size',type=int,default=4096);p.add_argument('--workers',type=int,default=16);p.add_argument('--limit-batches',type=int,default=0);p.add_argument('--seconds',type=int,default=3000);a=p.parse_args()
    if a.output.exists():raise ValueError('New evaluation identity required')
    if not 1<=a.batch_size<=8192 or not 0<=a.workers<=32 or not 1<=a.seconds<=3000 or a.limit_batches<0:raise ValueError('Bounds')
    a.output.mkdir();a.validation_batch_size=a.batch_size;a.validation_workers=a.workers
    import psutil,sys
    write_json(a.output/'process.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),command=sys.argv))
    a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');manifest=load_split(root/'phase1-v6-cohort-balanced-v4/split.json')
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text());sha=file_hash(a.checkpoint)
    if receipt['checkpoint_sha']!=sha:raise ValueError('Checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    if saved['contract']['split_hash']!=manifest['manifest_hash']:raise ValueError('Split changed')
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    total=manifest['counts']['validation'];total=min(total,a.limit_batches*a.batch_size) if a.limit_batches else total
    contract=dict(source=a.revision,checkpoint_sha=sha,split_hash=manifest['manifest_hash'],scope='profile' if a.limit_batches else 'full_validation',expected_windows=total,batch=a.batch_size,workers=a.workers,deadline_seconds=a.seconds)
    write_json(a.output/'manifest.json',contract)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        model=restore(saved).cuda().eval();metrics=Accumulator();generator=torch.Generator(device='cuda').manual_seed(917)
        source=device=None;began=time.perf_counter();status='failed';count=0;replay=None
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name,dir=str(a.output),config=contract)
        try:
            write_json(a.output/'progress.json',dict(stage='loading',windows=0,total=total,seconds=0,windows_per_second=0,eta_seconds=None))
            source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda');startup=time.perf_counter()-began
            index=0
            while count<total and (not a.limit_batches or index<a.limit_batches):
                if (a.output/'STOP').exists() or time.perf_counter()-began>a.seconds:raise InterruptedError('STOP/deadline; partial is not full evaluation')
                batch=device.pop();pos=positions(len(batch.grid),'cuda',16,generator)
                if len(batch.grid)==a.batch_size:
                    if replay is None:replay=Replay(model,metrics,batch,pos)
                    replay(batch,pos)
                else:metrics.add(model,batch,pos)
                count+=len(batch.grid)
                if index%25==0:
                    elapsed=time.perf_counter()-began;rate=count/elapsed
                    progress=dict(stage=contract['scope'],windows=count,total=total,seconds=elapsed,windows_per_second=rate,eta_seconds=(total-count)/max(1,rate),loader_wait_seconds=device.wait_seconds)
                    write_json(a.output/'progress.json',progress);w.log({'pipeline/'+k:v for k,v in progress.items() if k!='stage'},step=index)
                if count<total:device.advance()
                index+=1
            if not a.limit_batches and count!=total:raise ValueError('Incomplete coverage')
            detail,measured=metrics.result()
            if any(v['nonfinite'] for k,v in detail.items() if not k.startswith('activity_')):raise FloatingPointError('Nonfinite predictions')
            elapsed=time.perf_counter()-began
            write_json(a.output/'metrics.json',detail)
            result=dict(metrics=measured,windows=count,full_population=not a.limit_batches,seconds=elapsed,startup_seconds=startup,loader_wait_seconds=device.wait_seconds,peak_mib=torch.cuda.max_memory_allocated()/2**20,checkpoint_sha=sha,wandb_url=w.url,gradient_free_graph_parity='every metric accumulator')
            write_json(a.output/'result.json',result);w.log({'validation/'+k:v for k,v in measured.items()},step=index+1);status='complete';print(json.dumps({k:v for k,v in result.items() if k!='metrics'}),flush=True)
            write_json(a.output/'progress.json',dict(stage='complete',windows=count,total=total,seconds=elapsed,windows_per_second=count/elapsed,eta_seconds=0))
        except BaseException as exc:
            write_json(a.output/'failure.json',dict(error=repr(exc),windows=count,seconds=time.perf_counter()-began));raise
        finally:
            if device:device.close()
            if source:close_loader(source)
            w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1)


if __name__=='__main__':main()
