"""Controlled input-scaling experiment. Base V7 code and PPO settings unchanged."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
import sys
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import torch
from research.market_shards.v1.contract import sha
from research.rl_trading.v7 import train
from research.rl_trading.v7.model import Policy

RECIPE='price-relative-return-range-times100-log-activity-div10-v1'


class NormalizedPolicy(Policy):
    def forward(self,features,account,holding,candidate_valid,action_mask):
        shape=features.shape
        groups=features.reshape(*shape[:-1],7,14)
        # Constants are economic units, never validation-fitted statistics.
        scale=features.new_tensor([100.,100.,100.,.1,.1,1.,1.,1.,1.,1.,1.,1.,1.,1.])
        normalized=(groups*scale).reshape(shape)
        return super().forward(normalized,account,holding,candidate_valid,action_mask)


def main(argv=None):
    original_policy,original_identity=train.Policy,train.source_identity
    def identity():
        return dict(original_identity(),experiment_sha256=sha(Path(__file__)),experiment_recipe=RECIPE)
    train.Policy=NormalizedPolicy
    train.source_identity=identity
    try:
        return train.main(argv)
    finally:
        train.Policy=original_policy
        train.source_identity=original_identity


if __name__=='__main__':
    raise SystemExit(main())
