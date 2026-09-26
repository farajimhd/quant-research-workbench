"""Evaluate a frozen policy on later held-out sessions under its execution contract."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import argparse
from dataclasses import fields
import torch

from research.rl_trading.v2.config import Config
from research.rl_trading.v2.data import MarketSession
from research.rl_trading.v2.model import PortfolioPolicy
from research.rl_trading.v2.io import read, write, output_root, digest, file_hash, code_identity
from research.rl_trading.v2.train import evaluate


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--test-sessions',type=Path,nargs='+',required=True)
    p.add_argument('--checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--allow-segment',action='store_true')
    args = p.parse_args(argv)
    run = args.run.resolve()
    manifest = read(run/'run_manifest.json')
    if manifest['code']['files'] != code_identity()['files']:
        raise ValueError('Frozen evaluator code differs from training contract')
    sessions = [MarketSession.load(x,allow_segment=args.allow_segment) for x in args.test_sessions]
    used = [x['date'] for x in manifest['train']+manifest['validation']]
    if (len({x.plan['date'] for x in sessions}) != len(sessions)
            or any(x.plan['date'] <= max(used) for x in sessions)):
        raise ValueError('Held-out sessions must be unique and strictly later than train/validation')
    if any(x.plan['feature_names'] != manifest['feature_names'] for x in sessions):
        raise ValueError('Held-out feature schema changed')
    checkpoint = run/f'checkpoint_{args.checkpoint}.pt'
    saved = torch.load(checkpoint,map_location=args.device,weights_only=False)
    if saved['contract_hash'] != manifest['contract_hash']:
        raise ValueError('Checkpoint belongs to a different training contract')
    config = Config(**{f.name:manifest['config'][f.name] for f in fields(Config)})
    policy = PortfolioPolicy(**manifest['model']).to(args.device)
    policy.load_state_dict(saved['policy'])
    report = dict(run=str(run),checkpoint_hash=file_hash(checkpoint),config=config.manifest(),
        test_plans=[x.plan['plan_hash'] for x in sessions],results=evaluate(policy,sessions,config,args.device))
    root = output_root()/'evaluation'/digest(report)[:20]
    write(root/'report.json',report)
    print(root/'report.json',flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
