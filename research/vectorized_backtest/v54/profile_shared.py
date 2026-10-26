"""Bounded shared-core CPU/CUDA parity and CUDA-graph tile profiling."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,statistics
from pathlib import Path
import numpy as np
import torch
from .genome import sample,MARKET_INPUTS,CATALOG
from .network import NetworkPlan,state_values
from .network_cuda import signals
from .identity import source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
from research.vectorized_backtest.v42.publication import write_json

def profile(output,batch=128,width=453,iterations=30):
    output=require_runtime(output);output.mkdir(parents=True,exist_ok=True)
    if not torch.cuda.is_available():raise RuntimeError('Laptop CUDA required')
    torch.set_num_threads(1);members=sample(np.random.default_rng(2021),batch)
    p=NetworkPlan(members,'cuda');p.prepare(batch,width);g=torch.Generator().manual_seed(21)
    v=(torch.rand((width,MARKET_INPUTS),generator=g)*2-1).cuda();k=torch.ones((width,len(CATALOG)),device='cuda',dtype=torch.bool)
    position=torch.zeros((1,batch,width,9),device='cuda',dtype=torch.float64);opened=torch.zeros((1,batch,width),device='cuda',dtype=torch.float64);previous=torch.full((1,1,width),10.,device='cuda',dtype=torch.float64);index=torch.tensor(60,device='cuda')
    states=state_values(position,previous,opened,index.to(torch.float64),p.feedback)[0]
    out=torch.empty((1,batch,width),device='cuda',dtype=torch.int16)
    def reference():return p.reference(v,k,states)
    rows=[]
    def measure(fn,label):
        for _ in range(3):fn()
        torch.cuda.synchronize();p.reset();graph=torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph):fn()
        p.reset();durations=[]
        for _ in range(iterations):
            start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            start.record();graph.replay();end.record();end.synchronize();durations.append(start.elapsed_time(end))
        row=dict(path=label,median_ms=statistics.median(durations),min_ms=min(durations),max_ms=max(durations));rows.append(row)
        del graph
        return row
    # Reference is deliberately bounded: its expanded bank is not the optimized
    # production allocation and must not accidentally profile 6144 founders.
    baseline=measure(reference,'vectorized-torch-oracle')
    p.reset();expected=reference().clone();expected_ratios=p.ratios.clone();expected_memory=p.memory.clone()
    for tile in (16,32,64,128):
        p.cuda_block=tile;p.reset();actual=signals(p,v,k,position,opened,previous,index,out)
        if not torch.equal(expected,actual):raise ValueError('Profile action parity failed')
        torch.testing.assert_close(expected_ratios,p.ratios,rtol=1e-5,atol=3e-7)
        torch.testing.assert_close(expected_memory,p.export_state()['memory'].cuda(),rtol=1e-5,atol=4e-7)
        row=measure(lambda:signals(p,v,k,position,opened,previous,index,out),f'triton-tile-{tile}');row['tile']=tile
    best=min(rows[1:],key=lambda r:r['median_ms'])
    report=dict(source_sha256=source_hash(),device=torch.cuda.get_device_name(),batch=batch,width=width,hidden=64,heads=6,
        iterations=iterations,graph_replay=True,all_tiles_parity=True,measurements=rows,selected_tile=best['tile'],
        speedup=baseline['median_ms']/best['median_ms'],peak_cuda_bytes=torch.cuda.max_memory_allocated(),
        scope='neural step, synthetic bounded normalized inputs at Aug13 listing width; excludes projection and finance')
    write_json(output/'shared-profile.json',report);p.close();return report
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--batch',type=int,default=128);p.add_argument('--width',type=int,default=453);a=p.parse_args();print(json.dumps(profile(a.output,a.batch,a.width),indent=2))
if __name__=='__main__':main()
