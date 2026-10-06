"""Prepare the fixed six-day control from authenticated per-day exports.

This lets GPU fitting use completed TRAIN/calibration data while new public
development exports prepare. Those new days remain absent from this panel and
must be evaluated separately on the frozen selected checkpoint, not retrained.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.bias_panel import FOLDS,EXTENDED_FOLDS,VERSION,PRICE_HISTORY_VERSION,combine,normalization,verify_panel

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    original=args.original.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (original,output)) or output.exists():
        raise ValueError('Authenticated source and fresh laptop runtime output required')
    planned=original/'planned.json';scope=json.loads(planned.read_text());scope_hash=file_hash(planned)
    if scope['version'] not in (VERSION,PRICE_HISTORY_VERSION) or scope['folds']!={k:list(v) for k,v in EXTENDED_FOLDS.items()} or scope['sealed_labels_read'] is not False:
        raise ValueError('Unexpected extended public panel scope')
    prepared={};sessions={};sources={}
    for fold,days in FOLDS.items():
        items=[]
        for day in days:
            receipt=original/(day+'-receipt.json');path=original/(day+'.pt');proof=json.loads(receipt.read_text())
            if proof['scope_sha256']!=scope_hash or proof['fold']!=fold or file_hash(path)!=proof['sha256']:
                raise ValueError('Cached tensor/receipt scope changed')
            item=torch.load(path,map_location='cpu',weights_only=False);verify_panel({fold:item},scope)
            if (proof['session']['rows']!=len(item['action']) or proof['session']['counts']!=np.bincount(item['action'],minlength=4).tolist()
                or proof['session']['role']!=('development' if fold=='development' else 'train')):
                raise ValueError('Cached role/count mismatch')
            if scope['version']==PRICE_HISTORY_VERSION:
                used=np.unique(item['windows']);used=used[used>0]
                if not (item['features'][used,35]>.5).all():raise ValueError('Priced history contains unpriced rows')
            sessions[day]=proof['session'];sources[day]=dict(path=str(path),sha256=proof['sha256'],receipt_sha256=file_hash(receipt))
            items.append(item)
        prepared[fold]=combine(items)
    manifest=dict(scope,folds={k:list(v) for k,v in FOLDS.items()},extended_public_development=False,sessions=sessions,
        derived_from=dict(original_plan_sha256=scope_hash,exports=sources,new_public_development_opened=False),normalization=normalization(prepared['train']))
    manifest['hash']=digest(manifest);verify_panel(prepared,manifest)
    output.mkdir();torch.save(prepared,output/'panel.pt')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (output/'complete.json').write_text(json.dumps(dict(status='prepared',version=scope['version'],manifest_sha256=file_hash(output/'manifest.json'),
        panel_sha256=file_hash(output/'panel.pt'),counts={k:np.bincount(v['action'],minlength=4).tolist() for k,v in prepared.items()},sealed_labels_read=False),indent=2))
    print('Composed six authenticated exports; new development targets remain excluded',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
