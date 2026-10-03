"""Generate or verify the separate full-session swing-opportunity product."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

from research.rl_trading.v6.price_action_opportunities import Config,OUTPUT,build,product,build_bank_preview


def main():
    parser = argparse.ArgumentParser(description='NVDA 2026-07-31 RTH swing opportunity bands; no fills or training')
    parser.add_argument('--output-dir',type=Path,default=OUTPUT)
    parser.add_argument('--bank-root',type=Path,help='Reporting-certified repaired July 31 bank for audit preview')
    parser.add_argument('--half-life-seconds',type=float,default=30.)
    parser.add_argument('--quality-threshold',type=float,default=.9)
    parser.add_argument('--stop-offset',type=float,default=.01)
    args = parser.parse_args()
    config = Config(half_life_seconds=args.half_life_seconds,quality_threshold=args.quality_threshold,stop_offset=args.stop_offset)
    config.validate()
    if args.output_dir.resolve()==OUTPUT.resolve() and (OUTPUT/'complete.json').exists():
        proof,_=product()
        if proof['config']!=asdict(config):
            parser.error('Different producer parameters require a new --output-dir')
    else:
        proof=build_bank_preview(args.bank_root,args.output_dir,config) if args.bank_root else build(args.output_dir,config)
    print(json.dumps(dict(version=proof['version'],output_dir=str(args.output_dir),candles=proof['observed_price_candles'],reference_pairs=proof['trades'],actions=proof['actions']),indent=2))


if __name__=='__main__':
    main()
