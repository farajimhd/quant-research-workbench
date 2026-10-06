"""Reuse exact public exports under the fixed six-TRAIN diversity contract.

Only scope/runner metadata changes. Feature construction, target validation and
all source readers must match the authenticated producer before bytes link.
Original plans, tensor bytes and receipts remain immutable.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,ast,json,subprocess
from hashlib import sha256
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.bias_panel import PRICE_HISTORY_VERSION,PRICE_DIVERSITY_VERSION,verify_panel
from research.rl_trading.v6.reuse_bias_exports import function,same,tree

def verify_diversity_calculations(original,current):
    for name,raw in original.items():
        if name in ('bias_panel.py','run_bias_campaign.py'):continue
        if not same(tree(raw),tree(current[name])):raise ValueError('Unexpected source/reader change: '+name)
    for name in ('indexed_panel','combine','normalization'):
        if not same(function(original['bias_panel.py'],name),function(current['bias_panel.py'],name)):
            raise ValueError('Feature calculation changed: '+name)
    # Verify every tensor fence and rejection expression too. Only the explicit
    # static admitted-fold lookup differs; it admits no new sealed roles.
    old=function(original['bias_panel.py'],'verify_panel');new=function(current['bias_panel.py'],'verify_panel')
    def tensor_guards(node):
        return ast.Module(body=[s for s in node.body if not isinstance(s,ast.Assign)],type_ignores=[])
    if not same(tensor_guards(old),tensor_guards(new)):raise ValueError('Tensor or public rejection guards changed')
    for name in ('TeacherDecision','_validate'):
        if not same(function(original['training.py'],name),function(current['training.py'],name)):
            raise ValueError('Teacher target contract changed')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--producer-commit',required=True)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    original=args.original.resolve();output=args.output.resolve();source_root=Path(__file__).parent
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (original,output)) or original==output:
        raise ValueError('Distinct authenticated laptop runtime roots required')
    old=json.loads((original/'planned.json').read_text());new=json.loads((output/'planned.json').read_text())
    if old['version']!=PRICE_HISTORY_VERSION or new['version']!=PRICE_DIVERSITY_VERSION:
        raise ValueError('Only the explicit valid-price training-diversity transition is admitted')
    for key in ('tickers','seconds','input_history_contract','sealed_labels_read','scope'):
        if old[key]!=new[key]:raise ValueError('Input/ticker contract changed')
    verify_panel({},old);verify_panel({},new)
    if new['source_files_sha256']!={p.name:file_hash(p) for p in source_root.glob('*.py')}:
        raise ValueError('Fresh diversity source binding changed')
    commit=subprocess.check_output(['git','rev-parse',args.producer_commit+'^{commit}'],text=True).strip()
    previous={};current={}
    for name,expected in old['source_files_sha256'].items():
        if Path(name).name!=name or not name.endswith('.py'):raise ValueError('Invalid source path')
        current[name]=(source_root/name).read_bytes()
        matching=[current[name]] if sha256(current[name]).hexdigest()==expected else []
        for revision in (commit,old['source_commit']):
            if matching:break
            raw=subprocess.check_output(['git','show',revision+':research/rl_trading/v6/'+name],stderr=subprocess.PIPE)
            matching=[v for v in (raw,raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')) if sha256(v).hexdigest()==expected]
        if not matching:raise ValueError('Producer source cannot be reconstructed: '+name)
        previous[name]=matching[0]
    verify_diversity_calculations(previous,current);records=[]
    for fold,days in new['folds'].items():
        for day in days:
            if day not in old['folds'][fold]:continue
            path=original/(day+'.pt');receipt=original/(day+'-receipt.json');proof=json.loads(receipt.read_text())
            if proof['scope_sha256']!=file_hash(original/'planned.json') or proof['fold']!=fold or file_hash(path)!=proof['sha256']:
                raise ValueError('Original scope/fold/tensor bytes changed')
            if (output/path.name).exists() or (output/receipt.name).exists():raise ValueError('Refusing existing diversity exports')
            item=torch.load(path,map_location='cpu',weights_only=False);verify_panel({fold:item},old)
            if (len(item['action'])!=proof['session']['rows'] or np.bincount(item['action'],minlength=4).tolist()!=proof['session']['counts']
                or proof['session']['role']!=('development' if fold=='development' else 'train')):
                raise ValueError('Cached target counts/roles changed')
            records.append((path,receipt,proof));del item
    if not records:raise ValueError('No exact admitted exports')
    evidence=dict(version='rl-v6-diversity-export-reuse-v1',original_plan_sha256=file_hash(original/'planned.json'),
        new_plan_sha256=file_hash(output/'planned.json'),producer_commit=commit,feature_target_math_unchanged=True,
        old_source_sha256=old['source_files_sha256'],new_source_sha256=new['source_files_sha256'],original_artifacts_modified=False,
        days=[dict(day=path.name[:10],sha256=proof['sha256'],original_receipt_sha256=file_hash(receipt)) for path,receipt,proof in records])
    evidence['hash']=digest(evidence);(output/'reuse-proof.json').write_text(json.dumps(evidence,indent=2))
    for path,receipt,proof in records:
        os.link(path,output/path.name)
        if file_hash(output/path.name)!=proof['sha256']:raise ValueError('Linked bytes changed')
        proof.update(scope_sha256=evidence['new_plan_sha256'],reused_from=dict(path=str(path),receipt_sha256=file_hash(receipt),equivalence_sha256=file_hash(output/'reuse-proof.json')))
        (output/receipt.name).write_text(json.dumps(proof,indent=2))
    print('Authenticated diversity exports reused without feature recalculation:',len(records),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
