"""Authenticate unchanged panel calculations before reusing failed reader exports.

Original plans, receipts and tensors remain immutable. A fresh plan receives
hard links to verified cached bytes with an explicit source-equivalence proof.
Only the documented host-path reader fix and unrelated training controls may
differ; data builders and target validation must be identical ASTs.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,ast,copy,json,subprocess
from hashlib import sha256
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_panel import verify_panel

ALLOWED={'bias_panel.py','session_data.py','validation_split.py','training.py',
         'train.py','ticker_heads.py','run_laptop_teacher.py'}

def tree(raw):return ast.parse(raw.decode('utf-8-sig'))

def function(raw,name):
    return next(n for n in tree(raw).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name)

def same(a,b):return ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)

def verify_calculations(original,current):
    changed={n for n,raw in original.items() if sha256(raw).hexdigest()!=sha256(current[n]).hexdigest()}
    if not changed<=ALLOWED:raise ValueError('Unexpected producer source drift: '+str(sorted(changed-ALLOWED)))
    old=tree(original['bias_panel.py']);new=tree(current['bias_panel.py'])
    old.body=[n for n in old.body if not isinstance(n,ast.FunctionDef) or n.name!='main']
    new.body=[n for n in new.body if not isinstance(n,ast.FunctionDef) or n.name!='main']
    if not same(old,new):raise ValueError('Panel calculation or input contract changed')
    for name in ('TeacherDecision','_validate'):
        if not same(function(original['training.py'],name),function(current['training.py'],name)):
            raise ValueError('Teacher target construction/validation changed')
    old=function(original['session_data.py'],'open_session');new=copy.deepcopy(function(current['session_data.py'],'open_session'))
    new.args=copy.deepcopy(old.args)
    for node in ast.walk(new):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='dataset_split':
            node.keywords=[k for k in node.keywords if k.arg!='mapper']
    if not same(old,new):raise ValueError('Session reader changed beyond mapped split path')
    old=function(original['validation_split.py'],'dataset_split');new=copy.deepcopy(function(current['validation_split.py'],'dataset_split'))
    new.args=copy.deepcopy(old.args)
    for node in ast.walk(new):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='mapper':node.func.id='Path'
    if not same(old,new):raise ValueError('Split authentication changed beyond host-path mapping')
    for module,excluded in (('session_data.py','open_session'),('validation_split.py','dataset_split')):
        a=tree(original[module]);b=tree(current[module])
        a.body=[n for n in a.body if not isinstance(n,ast.FunctionDef) or n.name!=excluded]
        b.body=[n for n in b.body if not isinstance(n,ast.FunctionDef) or n.name!=excluded]
        if not same(a,b):raise ValueError('Other reader/data contracts changed')
    return sorted(changed)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--producer-commit',required=True)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    original=args.original.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (original,output)) or original==output:
        raise ValueError('Distinct laptop runtime roots required')
    old=json.loads((original/'planned.json').read_text());new=json.loads((output/'planned.json').read_text())
    ignored={'source_commit','source_files_sha256'}
    if {k:v for k,v in old.items() if k not in ignored}!={k:v for k,v in new.items() if k not in ignored}:
        raise ValueError('Frozen panel scope changed')
    commit=subprocess.check_output(['git','rev-parse',args.producer_commit+'^{commit}'],text=True).strip()
    previous={};current={};source_root=Path(__file__).parent
    for name,expected in old['source_files_sha256'].items():
        if Path(name).name!=name or not name.endswith('.py'):raise ValueError('Invalid source path')
        current[name]=(source_root/name).read_bytes()
        matching=[current[name]] if sha256(current[name]).hexdigest()==expected else []
        for revision in (commit,old['source_commit']):
            if matching:break
            raw=subprocess.check_output(['git','show',revision+':research/rl_trading/v6/'+name],stderr=subprocess.PIPE)
            candidates=(raw,raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
            matching=[r for r in candidates if sha256(r).hexdigest()==expected]
        if not matching:raise ValueError('Committed producer cannot reproduce original source bytes: '+name)
        previous[name]=matching[0]
    if new['source_files_sha256']!={f.name:file_hash(f) for f in source_root.glob('*.py')}:
        raise ValueError('Fresh plan source binding changed')
    changed=verify_calculations(previous,current);records=[]
    for receipt in sorted(original.glob('????-??-??-receipt.json')):
        day=receipt.name[:10];proof=json.loads(receipt.read_text());path=original/(day+'.pt')
        if proof['scope_sha256']!=file_hash(original/'planned.json') or file_hash(path)!=proof['sha256']:
            raise ValueError('Original cached bytes/scope changed')
        fold=proof['fold']
        if day not in new['folds'][fold] or (output/path.name).exists() or (output/receipt.name).exists():
            raise ValueError('Unexpected or existing cached day')
        item=torch.load(path,map_location='cpu',weights_only=False);verify_panel({fold:item},old)
        if len(item['action'])!=proof['session']['rows'] or np.bincount(item['action'],minlength=4).tolist()!=proof['session']['counts']:
            raise ValueError('Cached label counts changed')
        records.append((path,receipt,proof));del item
    if not records:raise ValueError('No authenticated exports to reuse')
    evidence=dict(version='rl-v6-bias-reader-fix-export-reuse-v1',producer_commit=commit,
        original_plan_sha256=file_hash(original/'planned.json'),new_plan_sha256=file_hash(output/'planned.json'),
        original_source_sha256=old['source_files_sha256'],new_source_sha256=new['source_files_sha256'],
        changed_modules=changed,calculation_ast_unchanged=True,original_artifacts_modified=False,
        days=[dict(day=p.name[:10],sha256=q['sha256'],original_receipt_sha256=file_hash(r)) for p,r,q in records])
    evidence['hash']=digest(evidence);(output/'reuse-proof.json').write_text(json.dumps(evidence,indent=2))
    for path,receipt,proof in records:
        os.link(path,output/path.name)
        if file_hash(output/path.name)!=proof['sha256']:raise ValueError('Linked bytes changed')
        proof.update(scope_sha256=evidence['new_plan_sha256'],reused_from=dict(path=str(path),receipt_sha256=file_hash(receipt),equivalence_sha256=file_hash(output/'reuse-proof.json')))
        (output/receipt.name).write_text(json.dumps(proof,indent=2))
    print('Reused authenticated exports without recalculation:',len(records),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
