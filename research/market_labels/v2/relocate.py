"""Byte-preserving, restart-safe relocation into the owning market dataset."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from research.market_shards.v1.contract import sha,write_json
from .contract import VERSION


def inventory(root):
    return {str(p.relative_to(root)).replace('\\','/'):sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def relocate(runtime,source,market):
    runtime=Path(runtime).resolve(strict=True);source=Path(source).resolve();market=Path(market).resolve(strict=True)
    target=(market/'labels'/'1a1b-spread-v2').resolve()
    if not source.is_relative_to(runtime) or not market.is_relative_to(runtime) or not target.is_relative_to(runtime):raise ValueError('Paths must remain inside the explicit runtime root')
    if source==target or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('Source and destination must be distinct non-nested directories')
    receipt=runtime/'market_shards'/'relocations'/'1a1b-spread-v2.json'
    registry=runtime/'v66-active-labels.json'
    if receipt.exists():
        record=json.loads(receipt.read_bytes())
        if record['source']!=str(source) or record['target']!=str(target):raise ValueError('Different relocation already recorded')
    else:
        if not source.is_dir() or target.exists():raise ValueError('Expected an existing source and absent destination')
        # Probe the producer lock before moving a completed product. The build
        # task is also required to be terminal before invoking this command.
        import msvcrt
        with (source/'writer.lock').open('r+b') as lock:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        doc=json.loads((source/'dataset.json').read_bytes())
        if doc['version']!=VERSION or not doc['full_inventory'] or doc['status']!='verified_review_only':raise ValueError('Complete spread-label dataset required')
        if Path(doc['source']).resolve()!=market:raise ValueError('Labels belong to a different market dataset')
        for role,expected in doc['binding']['source_manifests'].items():
            if sha(market/(role+'.json'))!=expected:raise ValueError('Market manifest binding differs')
        expected=sha(source/'dataset.json');current=json.loads(registry.read_bytes())
        if current!={'version':VERSION,'dataset':str(source/'dataset.json'),'sha256':expected}:raise ValueError('Active app registry does not match the source product')
        files=inventory(source)
        for day in doc['days']:
            proof_path=source/day['day']/'complete.json'
            if sha(proof_path)!=day['certificate']:raise ValueError('Day certificate changed')
            proof=json.loads(proof_path.read_bytes())
            if proof['binding']!=doc['binding']:raise ValueError('Day binding differs')
            for name,item in proof['files'].items():
                if files[day['day']+'/'+name]!=item['sha256']:raise ValueError('Label shard changed')
        record=dict(source=str(source),target=str(target),phase='prepared',files=files,dataset_sha256=expected,
            market_files={n:sha(market/n) for n in ('source.json','training.json','validation.json','complete.json')},old_registry=current)
        write_json(receipt,record)
    if source.exists():
        if target.exists():raise ValueError('Both locations exist; refusing merge or overwrite')
        if inventory(source)!=record['files']:raise ValueError('Source changed after preparation')
        target.parent.mkdir(parents=True,exist_ok=True)
        source.rename(target)  # Same-volume atomic directory rename; no copy/delete fallback.
    if not target.is_dir() or inventory(target)!=record['files']:raise ValueError('Destination integrity differs')
    if any(sha(market/n)!=h for n,h in record['market_files'].items()):raise ValueError('Market manifest bytes changed')
    record['phase']='verified';write_json(receipt,record)
    updated=dict(record['old_registry'],dataset=str(target/'dataset.json'))
    for path in (runtime/('v66-label-registry-'+VERSION+'.json'),registry):
        if path.exists() and json.loads(path.read_bytes()) not in (record['old_registry'],updated):raise ValueError('Concurrent registry change; refusing overwrite')
    write_json(runtime/('v66-label-registry-'+VERSION+'.json'),updated)
    write_json(registry,updated)
    record['phase']='complete';write_json(receipt,record)
    return dict(status=record['phase'],path=str(target),files=len(record['files']),sha256=record['dataset_sha256'],receipt=str(receipt))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    parser.add_argument('--source',type=Path,default=Path('D:/TradingML/runtimes/market_shards/labels/v66-1a1b-spread-v2'))
    parser.add_argument('--dataset',type=Path,default=Path('D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1'))
    args=parser.parse_args();print(json.dumps(relocate(args.runtime_root,args.source,args.dataset)))


if __name__=='__main__':main()
