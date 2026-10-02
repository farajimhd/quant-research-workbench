"""Reproduce the isolated one-ticker price-action experiment, never training."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.rl_trading.v6.price_action_labels import Config, OUTPUT, build, product


def main():
    parser = argparse.ArgumentParser(description='NVDA 2026-07-31 RTH price-action labels; no fills, fees or training')
    parser.add_argument('--output-dir',type=Path,default=OUTPUT)
    parser.add_argument('--half-life-seconds',type=float,default=30.)
    parser.add_argument('--stop-offset',type=float,default=.01)
    args = parser.parse_args()
    config = Config(half_life_seconds=args.half_life_seconds,stop_offset=args.stop_offset)
    config.validate()
    if args.output_dir.resolve() == OUTPUT.resolve() and (OUTPUT/'complete.json').exists():
        result, _ = product()
        if result['config'] != dict(timeframe_seconds=1,half_life_seconds=config.half_life_seconds,stop_offset=config.stop_offset):
            parser.error('Different parameters require a new --output-dir; completed products are immutable')
    else:
        result = build(args.output_dir,config)
    print(json.dumps(dict(version=result['version'],ticker=result['ticker'],day=result['day'],
        output_dir=str(args.output_dir),candles=result['observed_price_candles'],
        invalid_price_rows=result['omitted_invalid_price_rows'],trades=result['trades'],
        initial_discounted_value=result['initial_discounted_value']),indent=2))


if __name__ == '__main__':
    main()
