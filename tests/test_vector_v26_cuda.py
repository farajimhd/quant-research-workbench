"""Bounded same-device financial parity for packed sticky-owner replay."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v26.genome import sample
from research.vectorized_backtest.v26.composition import routed,MODES
from research.vectorized_backtest.v26.evaluator import replay_cohort,Execution
from research.vectorized_backtest.v7.qualify import synthetic

pytestmark=pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation required')


@pytest.mark.parametrize('mode',MODES)
def test_router_compiled_cuda_matches_same_device_eager_and_trace(mode):
    leaves=[replace(m,policy=replace(m.policy,swing_left=0,swing_right=0,stop_fraction=.1,target_fraction=.9,cooldown=1)) for m in sample(np.random.default_rng(26),2)]
    condition=leaves[0].rules['entry'] if mode=='conditional' else None
    members=[routed(mode,*leaves,condition),routed(mode,*reversed(leaves),condition)]
    left=torch.tensor([1,4,0,8,0,0],device='cuda',dtype=torch.int16)[None,:,None].expand(2,-1,-1)
    right=torch.tensor([1,2,1,0,0,0],device='cuda',dtype=torch.int16)[None,:,None].expand(2,-1,-1)
    gates=left|(right<<5)|(torch.tensor([1,2,2,1,2,2],device='cuda',dtype=torch.int16)[None,:,None]<<10)
    execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    expected=replay_cohort([synthetic('cuda')],members,[gates],execution=execution,episode_capacity=16,trace=True,backend='eager')[0]
    actual=replay_cohort([synthetic('cuda')],members,[gates],execution=execution,episode_capacity=16,trace=True,backend='compile')[0]
    for key in expected:
        if isinstance(expected[key],torch.Tensor):torch.testing.assert_close(actual[key],expected[key],rtol=0,atol=1e-10,equal_nan=True)
        else:assert actual[key]==expected[key],key
