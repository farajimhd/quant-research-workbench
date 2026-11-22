"""Bounded native GPU operator profile without modifying the training checkpoint."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .full_step import inputs,Replay
from .objectives import loss
from .train import optimizer
from ..phase1_v10.step import stable_clip
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--batch-size',type=int,default=1024)
    a=p.parse_args();a.device='cuda';a.resume=False;preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.cuda.set_per_process_memory_fraction(.85)
    root=a.output.parent
    with lease(a.output):
        saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);model=restore(saved).cuda();model.configure('independent')
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',saved['contract']['split_hash'],'cuda')
        rows=np.load(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r')
        batch=prepare_raw(rows[:a.batch_size],True).to('cuda');opt=optimizer(model,dict(price=3e-7,activity=1e-6))
        result=dict(source=a.source_revision,checkpoint_sha=file_hash(a.checkpoint),step=saved.get('step'),cases=[])
        for visible in (16,64):
            pp=positions(a.batch_size,'cuda',visible);ap=positions(a.batch_size,'cuda',56)
            replay=Replay(model,teacher,batch,pp,ap)
            for _ in range(5):replay(batch,pp,ap)
            torch.cuda.synchronize();start=time.perf_counter()
            for _ in range(32):replay(batch,pp,ap)
            torch.cuda.synchronize();seconds=(time.perf_counter()-start)/32
            del replay;model.zero_grad(set_to_none=True)
            def step():
                with torch.profiler.record_function('stage/preparation_teacher'):
                    x,v,raw,frames,anchor,p,q=inputs(teacher,batch,pp,ap)
                with torch.profiler.record_function('stage/encoder_decoder'):out,_=model(x,v,(p,q))
                with torch.profiler.record_function('stage/loss'):value,_=loss(model,out,x,v,raw,anchor,p,q)
                with torch.profiler.record_function('stage/backward'):value.backward()
                with torch.profiler.record_function('stage/clip'):
                    for _,g in model.groups():stable_clip(g)
                with torch.profiler.record_function('stage/optimizer'):opt.step()
            with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU,torch.profiler.ProfilerActivity.CUDA],record_shapes=True) as prof:
                step();torch.cuda.synchronize()
            prof.export_chrome_trace(str(a.output/f'trace-{visible}.json'))
            table=prof.key_averages(group_by_input_shape=True).table(sort_by='self_cuda_time_total',row_limit=45)
            (a.output/f'operators-{visible}.txt').write_text(table,encoding='utf-8')
            events=[dict(name=e.key,device_us=e.device_time_total,self_device_us=e.self_device_time_total,calls=e.count,shapes=e.input_shapes) for e in prof.key_averages(group_by_input_shape=True)]
            write_json(a.output/f'operators-{visible}.json',events)
            result['cases'].append(dict(visible=visible,graph_step_seconds=seconds,windows_per_second=a.batch_size/seconds))
            write_json(a.output/'result.json',result);print(json.dumps(result['cases'][-1]),flush=True)
            model.zero_grad(set_to_none=True)


if __name__=='__main__':main()
