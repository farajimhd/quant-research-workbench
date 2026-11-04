"""Frozen account replay for the single attribution-guided weight intervention."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))


def main(argv=None):
    from research.rl_trading.v7.experiments import entry_replay,entry_fast_replay
    from research.rl_trading.v7.experiments.manual_weight_edit import load_bundle
    def bound(*args,**kwargs):
        return entry_fast_replay.BoundAuthority(load_bundle(*args,**kwargs),'manual-weight-edit')
    previous=entry_replay.replay_session
    entry_replay.replay_session=entry_fast_replay.replay_session
    try:return entry_replay.main(argv,bundle_loader=bound)
    finally:entry_replay.replay_session=previous


if __name__=='__main__':raise SystemExit(main())
