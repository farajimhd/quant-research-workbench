"""Shared frozen account replay with the ordered-window temporal scorer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


def main(argv=None):
    from research.rl_trading.v7.experiments.temporal_net import load_bundle
    from research.rl_trading.v7.experiments.entry_replay import main as replay
    return replay(argv, bundle_loader=load_bundle)


if __name__ == '__main__':
    raise SystemExit(main())
