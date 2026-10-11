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


def exact_accounting(expected,actual):
    for name,value in expected.items():
        torch.testing.assert_close(value.cpu(),actual[name].cpu(),rtol=0,atol=0,msg=lambda message:name+': '+message)


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
        exact_accounting(original,cpu)
        for backend in ('eager','compile'):
            baseline=legacy([synthetic('cuda')],members,[gates.cuda()],execution=execution,backend=backend)[0]
            gpu=replay_cohort([synthetic('cuda')],members,[gates.cuda()],execution=execution,backend=backend)[0]
            # Version regression is bit exact on the same device/backend. V7 itself
            # differs across CPU/CUDA due to floating-point operation fusion.
            exact_accounting(baseline,gpu)
            for k,v in cpu.items():
                if isinstance(v,torch.Tensor):
                    torch.testing.assert_close(v,gpu[k].cpu(),rtol=1e-10,atol=1e-9,msg=lambda message:k+': '+message)
                else:
                    for expected,actual in zip(v,gpu[k]):
                        if k=='episode_listings':np.testing.assert_array_equal(expected,actual)
                        else:np.testing.assert_allclose(expected,actual,rtol=1e-10,atol=1e-9)
            # Ledger closes reconcile to the exact same-backend accounting.
            for i,profits in enumerate(gpu['episode_profits']):
                assert len(profits)==int(gpu['closed_positions'][i])
                np.testing.assert_allclose(sum(profits),float(gpu['net_pnl'][i]),rtol=1e-10,atol=1e-9)
            checks.append(dict(cost_bps=cost,backend=backend,exact_v7_v8_accounting=True,
                cross_device_rtol=1e-10,cross_device_atol=1e-9))
    write_json(root/'qualification.json',dict(status='passed',source_sha256=source_hash(),checks=checks))
    print(json.dumps(dict(status='passed',checks=checks)))


if __name__=='__main__':main()
