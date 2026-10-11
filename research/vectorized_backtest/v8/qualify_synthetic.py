"""CPU/V7 accounting and CUDA eager/compiled ledger qualification."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from dataclasses import replace
import numpy as np
import torch
from research.vectorized_backtest.v7.qualify import synthetic,member
from research.vectorized_backtest.v7.evaluator import replay_cohort as legacy
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,configure_caches,write_json
from .evaluator import replay_cohort,Execution
from .run_search import source_hash


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=require_runtime(a.output);configure_caches(root/'cache');torch.set_num_threads(1)
    members=[member(),replace(member(),minimum_age={'reduce':2}),replace(member(),policy=replace(member().policy,reduce_fraction=1.))]
    gates=torch.tensor([1,4,8,1,2,0],dtype=torch.uint8)[None,:,None].expand(3,-1,-1).clone()
    checks=[]
    for cost in (0,10):
        execution=Execution(cost_bps=cost,fee_per_share=.005,minimum_order_fee=1)
        cpu=replay_cohort([synthetic('cpu')],members,[gates],execution=execution)[0]
        original=legacy([synthetic('cpu')],members,[gates],execution=execution)[0]
        for k in original:torch.testing.assert_close(original[k],cpu[k],rtol=0,atol=0)
        for backend in ('eager','compile'):
            gpu=replay_cohort([synthetic('cuda')],members,[gates.cuda()],execution=execution,backend=backend)[0]
            for k,v in cpu.items():
                if isinstance(v,torch.Tensor):torch.testing.assert_close(v,gpu[k].cpu(),rtol=0,atol=0)
                else:
                    for expected,actual in zip(v,gpu[k]):np.testing.assert_array_equal(expected,actual)
            checks.append(dict(cost_bps=cost,backend=backend,exact=True))
    write_json(root/'qualification.json',dict(status='passed',source_sha256=source_hash(),checks=checks))
    print(json.dumps(dict(status='passed',checks=checks)))


if __name__=='__main__':main()
