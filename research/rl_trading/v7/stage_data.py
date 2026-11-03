"""Restart-safe, hash-verified local copy of training shards and past context only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import json
import shutil
import sys
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from research.market_shards.v1.contract import sha, write_json

DEFAULT_SOURCE = r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\market_shards\datasets\2026-top5-v1'
DEFAULT_DEST = 'D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1-training-local'


def stage(source, destination):
    source, destination = Path(source), Path(destination)
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not destination.resolve().is_relative_to(runtime):
        raise ValueError('Destination must be under available laptop runtime root')
    complete = json.loads((source/'complete.json').read_text())
    manifest = json.loads((source/'training.json').read_text())
    if sha(source/'training.json') != complete['manifests']['training']:
        raise ValueError('Training manifest hash mismatch')
    days = [row['day'] for row in manifest['sessions']]
    if len(days) != 150 or days != sorted(set(days)) or days[-1] != '2026-08-10':
        raise ValueError('Unexpected approved training split')
    context = sorted(set(days).union(*(set(r['daily']+r['hourly']) for r in manifest['sessions'])))
    if any(d > days[-1] for d in context):
        raise ValueError('Future context in training manifest')
    files = [(Path(n), sha(source/n)) for n in ('training.json', 'source.json', 'complete.json')]
    for kind, dates in (('bars', context), ('scanner', days)):
        for day in dates:
            folder = Path(kind)/day
            seal_path = source/folder/'complete.json'
            seal = json.loads(seal_path.read_text())
            expected = manifest['shard_seals'][day] if kind == 'bars' else sha(seal_path)
            if sha(seal_path) != expected or (kind == 'scanner' and seal['source_hash'] != manifest['source_hash']):
                raise ValueError('Source seal mismatch')
            files.append((folder/'complete.json', expected))
            for name, digest in seal['files'].items():
                if Path(name).name != name:
                    raise ValueError('Invalid sealed filename')
                files.append((folder/name, digest))
    destination.mkdir(parents=True, exist_ok=True)
    if (destination/'validation.json').exists():
        raise ValueError('Training-only destination contains validation manifest')
    copied = reused = total_bytes = 0
    for i, (relative, expected) in enumerate(files):
        if (destination/'STOP').exists():
            write_json(destination/'copy-status.json', dict(status='stopped', completed=i, queued=len(files)-i))
            return 2
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) != expected:
                raise ValueError('Existing destination differs: '+str(target))
            reused += 1
        else:
            temporary = target.with_name(target.name+'.partial')
            shutil.copyfile(source/relative, temporary)
            if sha(temporary) != expected:
                raise ValueError('Copied bytes differ: '+str(relative))
            os.replace(temporary, target)
            copied += 1
        total_bytes += target.stat().st_size
        if i % 100 == 0 or i+1 == len(files):
            record = dict(status='complete' if i+1 == len(files) else 'copying',
                          completed=i+1, queued=len(files)-i-1, copied=copied, reused=reused,
                          failed=0, bytes=total_bytes, training_days=len(days), context_days=len(context),
                          source=str(source), source_hash=manifest['source_hash'],
                          training_hash=complete['manifests']['training'])
            write_json(destination/'copy-status.json', record)
            print(json.dumps(record), flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=DEFAULT_SOURCE)
    parser.add_argument('--destination', default=DEFAULT_DEST)
    args = parser.parse_args()
    raise SystemExit(stage(args.source, args.destination))
