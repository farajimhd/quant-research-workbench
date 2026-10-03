"""Independent source census, exact recomputation and current-loader audit."""
import argparse
from datetime import date
import json
from pathlib import Path
import time

import numpy as np
import polars as pl
from polars.testing import assert_frame_equal

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.opportunity_dataset import (VERSION, ALGORITHM, FILES,
    verify_day, decoded_bars, load_teacher, write_json, require_dataset, reporting_plan)
from research.rl_trading.v6.price_action_opportunities import calculate, Config
from research.rl_trading.v6.bank import open_bank
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.split import CONTEXT_ONLY, TRAIN, DEVELOPMENT


def audit_and_publish(dataset_path, *, runtime_root, ranking_sort_secs=1):
    dataset_path=Path(dataset_path).resolve(); runtime=Path(runtime_root).resolve()
    if not dataset_path.is_relative_to(runtime): raise ValueError('Audit escaped runtime')
    data=json.loads(dataset_path.read_text())
    if (data['version']!=VERSION or data['algorithm']!=ALGORITHM or
        data.get('sealed_test_accessed') is not False or
        data.get('hash')!=digest({k:v for k,v in data.items() if k!='hash'})):
        raise ValueError('Audit accepts only a complete current-label generation')
    entries=[data['context']]+data['days']; expected=list(map(str,CONTEXT_ONLY+TRAIN+DEVELOPMENT))
    if [e['day'] for e in entries]!=expected: raise ValueError('Every saved bank must be represented')
    records=[]; started=time.time(); before=file_hash(dataset_path)
    for entry in entries:
        root=Path(entry['bank_root']); labels_root=Path(entry['teacher_root'])
        if not root.resolve().is_relative_to(runtime) or not labels_root.resolve().is_relative_to(runtime): raise ValueError('Source escaped runtime')
        if (file_hash(root/'complete.json')!=entry['bank_certificate_sha256'] or
            file_hash(labels_root/'complete.json')!=entry['teacher_sha256']): raise ValueError('Pinned source changed')
        reporting_plan(root, entry['day'])
        proof=verify_day(labels_root,entry['bank_certificate_sha256'])
        bank=open_bank(root/'bank',verify_hashes=False)
        for name in ('close_us.npy','scalar.npy'):
            if file_hash(root/'bank'/name)!=bank.manifest['files_sha256'][name]: raise ValueError('Actual price/clock bank bytes changed')
        valid=invalid=0
        for left in range(0,len(bank.scalar),65536):
            raw=bank.scalar[left:left+65536]
            price_valid=(raw[:,35]==1)&(raw[:,36]==1)
            valid+=int(price_valid.sum()); invalid+=int((~price_valid).sum())
            if np.any(raw[price_valid,20]!=1): raise ValueError('Unaccounted unavailable MACD')
        identities=tuple(sorted(bank.manifest['offsets']))
        if (tuple(proof['identities'])!=identities or proof['activity_rows']!=len(bank.close_us) or
            proof['valid_rows']!=valid or proof['invalid_price_rows']!=invalid):
            raise ValueError('Label coverage differs from independently scanned full source population')
        lengths={identity:right-left for identity,(left,right) in bank.manifest['offsets'].items()}
        samples=list(dict.fromkeys([max(lengths,key=lengths.get),min((s for s in lengths if lengths[s]>0),key=lengths.get)]))
        compared=[]
        for identity in samples:
            bars,_=decoded_bars(bank.listing(identity))
            if not bars.height: continue
            expected_frames=calculate(bars,Config(**proof['config']))
            shard=next(s for s in proof['shards'] if identity in json.loads((labels_root/s['path']/'complete.json').read_text())['identities'])
            for name,expected_frame in zip(FILES,expected_frames):
                actual=pl.scan_parquet(labels_root/shard['path']/(name+'.parquet')).filter(pl.col('listing_id')==identity).collect().drop('listing_id')
                if expected_frame.width:
                    assert_frame_equal(actual.select(expected_frame.columns),expected_frame,check_dtypes=False,check_exact=True)
                elif actual.height: raise ValueError('Unexpected pair/trade for no-opportunity source')
            compared.append(dict(listing_id=identity,candles=bars.height))
        loader_rows=None
        if entry['day'] in (str(TRAIN[0]),str(DEVELOPMENT[-1])):
            session=open_session(root,runtime_root=runtime,previous_root=Path(entry['previous_root']))
            labels,outcomes=load_teacher(labels_root,session,runtime_root=runtime,audit_development=True,audit_listing_ids=[samples[0]])
            if outcomes or any(d.label_version!=ALGORITHM for d in labels): raise ValueError('Legacy labels reached teacher adapter')
            loader_rows=len(labels); del labels,session
        row=dict(day=entry['day'],activity_rows=len(bank.close_us),valid_rows=valid,invalid_price_rows=invalid,
            recomputed_listings=compared,real_teacher_loader_rows=loader_rows)
        records.append(row); print(json.dumps(dict(status='audited_day',**row)),flush=True); del bank
    # Preserve the pre-existing one-second ranking setting; it does not enter
    # label generation. Record this publication setting separately from shards.
    data['ranking']['sort_secs']=ranking_sort_secs
    audit=dict(status='passed',algorithm=ALGORITHM,sealed_test_accessed=False,records=records,
        day_certificates={e['day']:e['teacher_sha256'] for e in entries},ranking=data['ranking'],
        generation_dataset_sha256=before,source_file_sha256=file_hash(Path(__file__)),seconds=time.time()-started,
        totals={key:sum(r[key] for r in records) for key in ('activity_rows','valid_rows','invalid_price_rows')})
    audit_path=dataset_path.parent/'publication-audit.json'; write_json(audit_path,audit)
    data['publication_audit']=str(audit_path); data['publication_audit_sha256']=file_hash(audit_path)
    data['hash']=digest({k:v for k,v in data.items() if k!='hash'}); write_json(dataset_path,data)
    require_dataset(dataset_path,runtime_root=runtime)
    write_json(runtime/'rl-v6-active-labels.json',dict(version=VERSION,algorithm=ALGORITHM,
        dataset=str(dataset_path),sha256=file_hash(dataset_path),publication_audit_sha256=data['publication_audit_sha256']))
    print(json.dumps(dict(status='publication_audit_passed',totals=audit['totals'],seconds=audit['seconds'])),flush=True)
    return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    args=parser.parse_args(argv)
    audit_and_publish(args.dataset,runtime_root=args.runtime_root)


if __name__=='__main__': main()
