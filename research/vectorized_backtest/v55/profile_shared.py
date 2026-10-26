"""Profile actual-size recurrent storage and tile launches at Aug13 listing width."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,statistics
from dataclasses import replace
from pathlib import Path
import numpy as np
import torch
from .genome import sample,MARKET_INPUTS,CATALOG
from .network import NetworkPlan,state_values
from .network_cuda import signals
from .structure import grow,prune
from .composition import proposal
from .identity import source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
from research.vectorized_backtest.v42.publication import write_json

def resize(member,size,seed=1):
    net=member.networks['shared'];r=proposal(np.random.default_rng(seed),'profile-growth')
    if size<len(net.nodes):net=prune(net,tuple(range(size,len(net.nodes))))
    while len(net.nodes)<size:net=grow(r,net,max(x for x in (1,2,4,8) if x<=size-len(net.nodes)))
    return replace(member,networks={'shared':net}).validate()

def profile(output,batch=128,width=453,iterations=15):
    output=require_runtime(output);output.mkdir(parents=True,exist_ok=True)
    if not torch.cuda.is_available():raise RuntimeError('Laptop CUDA required')
    torch.set_num_threads(1);base=sample(np.random.default_rng(2021),batch);g=torch.Generator().manual_seed(21)
    v=(torch.rand((width,MARKET_INPUTS),generator=g)*2-1).cuda();k=torch.ones((width,len(CATALOG)),device='cuda',dtype=torch.bool)
    position=torch.zeros((1,batch,width,9),device='cuda',dtype=torch.float64);opened=torch.zeros((1,batch,width),device='cuda',dtype=torch.float64);previous=torch.full((1,1,width),10.,device='cuda',dtype=torch.float64);index=torch.tensor(60,device='cuda')
    cases=[]
    for name,widths in (('fixed-64',[64]),('mixed',[16,64,65,128,129,256,257,496,506])):
        members=[resize(m,widths[i%len(widths)],i+1) for i,m in enumerate(base)]
        p=NetworkPlan(members,'cuda');p.prepare(batch,width);out=torch.empty((1,batch,width),device='cuda',dtype=torch.int16)
        states=state_values(position,previous,opened,index.to(torch.float64),p.feedback)[0];rows=[]
        def measure(fn,label):
            for _ in range(3):fn()
            torch.cuda.synchronize();p.reset();graph=torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph):
                for _ in range(8):fn()
            p.reset();times=[]
            for _ in range(64):graph.replay()
            torch.cuda.synchronize()
            for _ in range(iterations):
                a=torch.cuda.Event(enable_timing=True);b=torch.cuda.Event(enable_timing=True);a.record();graph.replay();b.record();b.synchronize();times.append(a.elapsed_time(b)/8)
            row=dict(path=label,median_ms=statistics.median(times),min_ms=min(times),max_ms=max(times));rows.append(row);del graph;return row
        measure(lambda:p.reference(v,k,states),'torch-oracle')
        p.reset();expected=p.reference(v,k,states).clone();ratios=p.ratios.clone();memory=p.memory.clone()
        for h in (32,64,128):
            for t in (16,32):
                p.core_tile=h;p.cuda_block=t;p.reset();actual=signals(p,v,k,position,opened,previous,index,out)
                if not torch.equal(expected,actual):raise ValueError('Profile action parity failed')
                torch.testing.assert_close(ratios,p.ratios,rtol=1e-5,atol=3e-7);torch.testing.assert_close(memory,p.export_state()['memory'].cuda(),rtol=1e-5,atol=4e-7)
                row=measure(lambda:signals(p,v,k,position,opened,previous,index,out),f'ragged-h{h}-u{t}');row.update(core_tile=h,listing_tile=t)
        best=min(rows[1:],key=lambda r:r['median_ms']);p.core_tile=best['core_tile'];p.cuda_block=best['listing_tile'];original=p.core_tiles[p.core_tile]
        padded=np.array([(b,h) for b in range(batch) for h in range(0,int(max(p.counts_cpu)),p.core_tile)],np.int32)
        p.core_tiles[p.core_tile]=torch.as_tensor(padded,device='cuda')
        row=measure(lambda:signals(p,v,k,position,opened,previous,index,out),'cohort-maximum-tile-launch')
        p.reset();actual=signals(p,v,k,position,opened,previous,index,out)
        if not torch.equal(actual,expected):raise ValueError('Padded launch parity failed')
        p.core_tiles[p.core_tile]=original
        if name=='fixed-64' and not .7<=row['median_ms']/best['median_ms']<=1.3:raise RuntimeError('Identical-work timing control unstable; repeat profile')
        cases.append(dict(case=name,batch=batch,width=width,min_core=int(min(p.counts_cpu)),max_core=int(max(p.counts_cpu)),total_core=p.total_nodes,
            measurements=rows,selected=best,oracle_speedup=rows[0]['median_ms']/best['median_ms'],
            launch_padding_speedup=row['median_ms']/best['median_ms'],ragged_core_tiles=original.shape[0],padded_core_tiles=len(padded),
            recurrent_buffer_bytes=p.total_nodes*width*10,padded_recurrent_buffer_bytes=batch*int(max(p.counts_cpu))*width*10,all_parity=True))
        p.close();del p,states,memory,ratios,expected,out;torch.cuda.empty_cache()
    mixed=cases[-1]['selected'];report=dict(source_sha256=source_hash(),device=torch.cuda.get_device_name(),cases=cases,iterations=iterations,captured_steps=8,warm_replays=64,graph_replay=True,
        selected_core_tile=mixed['core_tile'],selected_listing_tile=mixed['listing_tile'],scope='Neural step; excludes feature projection, finance and serialization')
    write_json(output/'shared-profile.json',report);return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--batch',type=int,default=128);p.add_argument('--width',type=int,default=453);a=p.parse_args();print(json.dumps(profile(a.output,a.batch,a.width),indent=2))
if __name__=='__main__':main()
