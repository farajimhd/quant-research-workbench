"""Prepare bounded public TRAIN prefixes with the full certified population.

No model fitting, development targets, sealed targets, or bar recalculation.
Completed per-day caches are hash-verified on resume; incomplete days rebuild
only their own unpublished cache files after reauthenticating the source.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from datetime import datetime
import gc
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import torch
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.opportunity_dataset import load_teacher
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.ranked_teacher_data import coverage_report,load_prepared
from research.rl_trading.v6.session_data import open_session

VERSION='rl-v6-ranked-public-train-preparation-v1'


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');temporary.replace(path)


def save_cache(root,session,targets,scope,binding):
    if (session.role!='train' or list(session.listings)!=binding['input_listings'] or
            session.day.isoformat()!=binding['day'] or
            session.source_certificate_sha256!=binding['bank_certificate_sha256'] or
            session.context_split_receipt_sha256!=binding['context_split_receipt_sha256']):
        raise ValueError('Only the complete TRAIN population may be cached')
    root.mkdir(exist_ok=True)
    plan=dict(version=VERSION,arguments=dict(day=binding['day'],seconds=binding['seconds']),
        target_listing_ids=binding['target_listing_ids'],
        **{k:binding[k] for k in ('dataset_sha256','market_dataset_sha256','bank_certificate_sha256',
            'market_certificate_sha256','context_split_receipt_sha256','input_listings')})
    plan['hash']=digest(plan);write(root/'source.json',plan)
    temporary=root/'prepared-train.pt.tmp'
    torch.save(dict(session=session,targets=targets,scope=scope),temporary)
    temporary.replace(root/'prepared-train.pt')
    write(root/'prepared-train.json',dict(version='rl-v6-ranked-verified-train-cache-v1',
        sha256=file_hash(root/'prepared-train.pt'),source_binding=binding))
    load_prepared(root,plan)  # Authenticate the real serialized cache, not just its receipt.
    return plan


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-runtime',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--days',nargs='+',default=['2026-07-31','2026-08-03','2026-08-04','2026-08-05','2026-08-06','2026-08-07'])
    parser.add_argument('--tickers',nargs='+',required=True)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();output=args.output.resolve()
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or
            args.days!=sorted(set(args.days)) or not 1<=len(args.days)<=6 or
            not 1<=args.seconds<=600 or not 1<=len(set(args.tickers))==len(args.tickers)<=19):
        raise ValueError('Bounded ordered public TRAIN preparation under laptop runtime required')
    from research.rl_trading.v6 import saved_label_audit as source,published_market_audit as market
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME']=str(args.source_runtime.resolve())
    # Validate ALL roles before opening any target shard.
    entries=[]
    for day in args.days:
        active,entry,_,teacher,bankroot,_=source.session(day)
        ma,_,me,mroot,_=market.session(day)
        if entry['role']!='train':raise ValueError('Preparation accepts public TRAIN only: '+day)
        entries.append((day,active,entry,teacher,bankroot,ma,me,mroot))
    if len({(x[1]['sha256'],x[5]['sha256']) for x in entries})!=1:
        raise ValueError('Public dataset identity changed during preflight')
    manifest=dict(version=VERSION,days=args.days,tickers=args.tickers,seconds=args.seconds,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        producer_sha256=file_hash(Path(__file__)),source_runtime=str(args.source_runtime.resolve()),
        dataset_sha256=entries[0][1]['sha256'],market_dataset_sha256=entries[0][5]['sha256'],
        sealed_labels_read=False,development_labels_read=False,training_run=False)
    # Commit may advance through unrelated work; producer/data/recipe must remain exact.
    if output.exists():
        if not args.resume:raise ValueError('Existing preparation requires explicit --resume')
        saved=json.loads((output/'manifest.json').read_text())
        if saved['hash']!=digest({k:v for k,v in saved.items() if k!='hash'}) or any(
                saved.get(k)!=v for k,v in manifest.items() if k!='source_commit'):
            raise ValueError('Preparation recipe/source/data changed')
    else:
        output.mkdir();manifest['hash']=digest(manifest);write(output/'manifest.json',manifest)
    completed=[];torch.set_num_threads(2)
    try:
        for day,active,entry,teacher,bankroot,ma,me,mroot in entries:
            write(output/'progress.json',dict(active=day,completed=completed,queued=args.days[len(completed)+1:],failed=[]))
            names=source.saved_symbols(str(bankroot),entry['bank_certificate_sha256'])
            ids=sorted(i for i,name in names.items() if name in args.tickers)
            if len(ids)!=len(args.tickers):raise ValueError('Target tickers must resolve uniquely: '+day)
            root=output/day
            if (root/'prepared-train.json').exists():
                prior=json.loads((root/'source.json').read_text())
                if (prior['hash']!=digest({k:v for k,v in prior.items() if k!='hash'}) or
                        prior['arguments']!=dict(day=day,seconds=args.seconds) or prior['target_listing_ids']!=ids or
                        prior['dataset_sha256']!=active['sha256'] or prior['market_dataset_sha256']!=ma['sha256'] or
                        prior['bank_certificate_sha256']!=entry['bank_certificate_sha256'] or prior['market_certificate_sha256']!=me['sha256']):
                    raise ValueError('Completed cache authority changed: '+day)
                packed,targets,scope=load_prepared(root,prior)
            else:
                full=open_session(bankroot,runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']))
                labels,_=load_teacher(teacher,full,runtime_root=source.runtime(),audit_development=True,audit_listing_ids=ids,market_root=mroot)
                begin=int(datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
                packed,targets=subset(full,labels,full.listings,begin,begin+args.seconds*1_000_000-1)
                if packed.listings!=full.listings or not targets:raise ValueError('Full population and nonempty targets required')
                scope=dict(day=day,role='train',input_listings=list(packed.listings),target_ids=ids,begin_us=begin,
                    end_us=begin+args.seconds*1_000_000,coverage=coverage_report(targets,len(packed.listings)),
                    bank_certificate_sha256=entry['bank_certificate_sha256'],market_certificate_sha256=me['sha256'],
                    context_split_receipt_sha256=packed.context_split_receipt_sha256)
                binding=dict(dataset_sha256=active['sha256'],market_dataset_sha256=ma['sha256'],
                    bank_certificate_sha256=entry['bank_certificate_sha256'],market_certificate_sha256=me['sha256'],
                    context_split_receipt_sha256=packed.context_split_receipt_sha256,input_listings=list(packed.listings),
                    day=day,seconds=args.seconds,target_listing_ids=ids)
                del full,labels;gc.collect();save_cache(root,packed,targets,scope,binding)
            completed.append(dict(day=day,decisions=len(targets),input_listings=len(packed.listings),
                cache_sha256=file_hash(root/'prepared-train.pt'),source_sha256=file_hash(root/'source.json')))
            del packed,targets;gc.collect()
            print(json.dumps(completed[-1]),flush=True)
        write(output/'complete.json',dict(status='completed',sessions=completed,training_run=False,sealed_labels_read=False))
        write(output/'progress.json',dict(active=None,completed=completed,queued=[],failed=[]))
    except Exception as error:
        write(output/'failure.json',dict(completed=completed,error=str(error)));raise
    return 0


if __name__=='__main__':raise SystemExit(main())
