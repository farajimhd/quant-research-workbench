"""Isolate complete own-trajectory credit: gamma=lambda=1, one episode per PPO rollout."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from dataclasses import replace
from pathlib import Path
import sys
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from research.market_shards.v1.contract import sha
from research.rl_trading.v7 import train


def main(argv=None):
    original_config,original_identity=train.Config,train.source_identity
    def config():
        return replace(original_config(),gamma=1.,gae_lambda=1.,rollout=60000)
    def identity():
        return dict(original_identity(),experiment_sha256=sha(Path(__file__)),
                    experiment_recipe='whole-own-episode-undiscounted-credit-v1')
    train.Config=config
    train.source_identity=identity
    try:
        return train.main(argv)
    finally:
        train.Config=original_config
        train.source_identity=original_identity


if __name__=='__main__':
    raise SystemExit(main())
