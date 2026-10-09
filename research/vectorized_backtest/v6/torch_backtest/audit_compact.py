"""Training-only sparse input integrity, fresh certificate and pointer audit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import polars as pl
from .compact_prepare import VERSION,KEY_STRIDE
from .runtime import file_hash,require_runtime
from .materialize import write_json
from .feature_bank import CertifiedBank,CATALOG


def audit(root,sessions,emit=print):
    root=Path(root);spec=json.loads(Path(sessions).read_text());receipt=json.loads((root/'complete.json').read_text())
    training={s['day']:s for s in spec['training']}
    if len(training)!=30 or len(receipt['receipts'])!=30 or receipt['version']!=VERSION or receipt['status']!='complete' or not receipt['ready_for_replay'] or receipt['validation_opened'] or receipt['failures'] or (root/'owner.lock').exists():raise ValueError('Preparation has not completed and released ownership')
    if receipt['sessions_sha256']!=file_hash(sessions) or set(training)!={r['day'] for r in receipt['receipts']} or set(training)&{s['day'] for s in spec['validation']}:raise ValueError('Training coverage or frozen split changed')
    results=[]
    for entry in receipt['receipts']:
        folder=root/entry['day'];path=folder/'complete.json';day=json.loads(path.read_text());item=training[entry['day']]
        if file_hash(path)!=entry['sha256'] or day['identity']['session']!=item or day['validation_opened'] or not day['ready_for_replay']:raise ValueError('Day receipt/session changed')
        for name,checksum in day['files'].items():
            target=(folder/name).resolve()
            if target.parent!=folder.resolve() or file_hash(target)!=checksum:raise ValueError('Prepared file integrity failed: '+name)
        bank=CertifiedBank(item['feature_root'],expected_day=item['day'])
        if bank.certificate_hash!=day['bank_certificate_sha256'] or file_hash(item['split_certificate'])!=day['split_certificate_sha256']:raise ValueError('Current opening certificate changed')
        if item.get('previous_feature_root'):
            prior=CertifiedBank(item['previous_feature_root'])
            if prior.certificate_hash!=day['prior_certificate_sha256'] or file_hash(item['previous_split_certificate'])!=day['prior_split_certificate_sha256']:raise ValueError('Prior opening certificate changed')
        mapping=json.loads(Path(item['identity_map']).read_text())
        if mapping['bank_certificate_sha256']!=bank.certificate_hash or any(mapping['listing_to_ticker'].get(v['listing_id'])!=v['ticker'] for v in day['listings']):raise ValueError('Listing identity changed')
        feature=np.load(folder/'features.npy',mmap_mode='r');valid=np.load(folder/'feature_valid.npy',mmap_mode='r')
        keys=np.load(folder/'feature_keys.npy',mmap_mode='r');market=pl.read_parquet(folder/'market.parquet');top=pl.read_parquet(folder/'top_blocks.parquet')
        if feature.shape!=(day['feature_rows'],len(CATALOG)) or valid.shape!=feature.shape or feature.dtype!=np.float32 or valid.dtype!=np.bool_ or np.any(np.diff(keys)<=0):raise ValueError('Feature shapes/order invalid')
        if market.height!=day['market_rows'] or not np.array_equal(market['source_row'].to_numpy(),np.arange(market.height)):raise ValueError('Market row pointer identity invalid')
        if top['source_row'].null_count() or top['feature_row'].null_count():raise ValueError('Top-N row lacks source/history evidence')
        source=top['source_row'].to_numpy();features=top['feature_row'].to_numpy();clock=top['clock'].to_numpy();listing=top['listing'].to_numpy()
        if np.any(source>=market.height) or np.any(features>=len(keys)) or np.any(source<0) or np.any(features<0):raise ValueError('Pointer outside certified data')
        if not np.array_equal(market['listing'].to_numpy()[source],listing) or np.any(market['clock'].to_numpy()[source]>clock) or not np.array_equal(keys[features]//KEY_STRIDE,listing) or np.any(keys[features]%KEY_STRIDE>clock):raise ValueError('Top-N pointer crosses identity or future boundary')
        results.append(dict(day=item['day'],receipt_sha256=entry['sha256'],market_rows=market.height,feature_rows=len(keys),top_rows=top.height))
        emit(json.dumps(dict(stage='Input audit',completed=len(results),total=30,day=item['day'])),flush=True)
    result=dict(status='passed',version='v6-sparse-input-audit-v1',root_receipt_sha256=file_hash(root/'complete.json'),sessions_sha256=file_hash(sessions),training=results,validation_opened=False,broker_replay_qualified=False)
    write_json(require_runtime(root)/'input_audit.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--sessions',type=Path,required=True)
    args=parser.parse_args();audit(args.output,args.sessions)


if __name__=='__main__':main()
