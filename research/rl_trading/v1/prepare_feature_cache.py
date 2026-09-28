"""Prepare hash-bound, temporary float16 staging for certified RL sessions."""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True

import argparse
from pathlib import Path

from research.rl_trading.v1.data import SessionShard
from src.runtime_paths import runtime_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shards',type=Path,nargs='+',required=True)
    parser.add_argument('--cache-root',type=Path,required=True)
    args = parser.parse_args(argv)
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    cache_root = args.cache_root.resolve()
    if not cache_root.is_relative_to(runtime):
        raise ValueError('Feature cache must stay inside the runtime root')
    for path in args.shards:
        shard = SessionShard(path)
        certificate = shard.build_feature_cache(cache_root)
        print(f'{shard.plan["date"]} | {shard.plan["plan_hash"]} | '
            f'{certificate["cache_hash"]}',flush=True)
        shard.release_mapped_pages()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
