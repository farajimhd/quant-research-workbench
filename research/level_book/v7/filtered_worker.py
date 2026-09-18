"""One restart-safe filtered ticker rebuild; progress uses campaign artifacts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_name] = '1'
import argparse
from pathlib import Path
from types import SimpleNamespace


def main():
    from .campaign import worker, exclusive, paths, load_env_files, discover_clickhouse_env_files
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--before', help='Prepare only source sessions preceding this ISO session date')
    args = parser.parse_args()
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    print('Preparing filtered V7 history for '+args.ticker, flush=True)
    # Serialize concurrent requests for the same immutable successor; an exited
    # process releases the OS lock, while completed receipts survive cancellation.
    while True:
        lock = exclusive(args.runtime/'preparation.lock')
        try:
            lock.__enter__()
        except OSError as exc:
            if getattr(exc, 'errno', None) not in (11, 13, 36):
                raise
            time.sleep(1)
            continue
        try:
            if not (paths(args.runtime, args.ticker)/'ready.json').exists():
                if args.before:
                    from .filtered_prefix import worker as prefix_worker
                    prefix_worker(SimpleNamespace(runtime=args.runtime,ticker=args.ticker,threads=1,before=args.before))
                else:
                    worker(SimpleNamespace(runtime=args.runtime, ticker=args.ticker, threads=1))
        finally:
            lock.__exit__(None, None, None)
        break
    print('Verified filtered V7 history for '+args.ticker, flush=True)


if __name__ == '__main__':
    main()
