"""Deterministic receipt/objective audit; never opens unscored holdout inputs."""
import argparse,json
from pathlib import Path
import torch
from .runtime import file_hash,write_json
from .run_search import restore,state,fingerprint,clean
from .stability import Objective,score
from .feature_bank import CATALOG

def audit(root):
    root=Path(root);identity=json.loads((root/'identity.json').read_text());objective=Objective(**identity['objective'])
    verified=0
    for folder in sorted(root.glob('generation_*')):
        if not (folder/'generation.json').exists():continue
        generation=json.loads((folder/'generation.json').read_text());population=[restore(v) for v in generation['population']]
        expected=fingerprint([state(v) for v in population]);results=[];dates=[]
        if expected!=generation['population_sha256']:raise ValueError('Generation population fingerprint changed')
        for item in generation['receipts']:
            path=Path(item['path'])
            if file_hash(path)!=item['sha256']:raise ValueError('Session receipt hash changed')
            r=json.loads(path.read_text());dates.append(r['day']);results.append(r['metrics'])
            if r['population_sha256']!=expected or file_hash(path.parent/'fills.pt')!=r['ledger_sha256']:raise ValueError('Session population/ledger hash changed')
        if dates!=[s['day'] for s in identity['sessions']['training']]:raise ValueError('Generation does not cover exact all30 training dates')
        def tensor(name):return torch.tensor([r[name] for r in results],dtype=torch.float64)
        complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in v.programs().values()) for v in population],dtype=torch.float64)
        calculated=score(tensor('net_pnl'),tensor('drawdown'),tensor('stop_risk_dollar_seconds'),tensor('capital_dollar_seconds'),tensor('filled_batches'),tensor('terminal_valid'),complexity,config=objective)
        original=generation['scores']
        torch.testing.assert_close(calculated['score'],torch.tensor(original['score'],dtype=torch.float64),rtol=1e-12,atol=1e-12)
        if calculated['feasible'].tolist()!=original['feasible']:raise ValueError('Feasibility arithmetic changed')
        verified+=1
    freeze=root/'frozen_winner.json';validation=0
    if freeze.exists():
        frozen=json.loads(freeze.read_text());h=file_hash(freeze)
        if frozen['identity_sha256']!=file_hash(root/'identity.json'):raise ValueError('Frozen winner identity changed')
        for folder in sorted(root.glob('validation_*')):
            receipt=folder/'receipt.json'
            if not receipt.exists():continue
            r=json.loads(receipt.read_text())
            if r['freeze_sha256']!=h or r['evaluation_epoch']<=frozen['frozen_epoch'] or file_hash(folder/'fills.pt')!=r['ledger_sha256']:raise ValueError('Freeze/evaluation order or ledger binding invalid')
            validation+=1
    report=dict(status='passed',completed_generations_verified=verified,validation_sessions_verified=validation,identity_sha256=file_hash(root/'identity.json'),full_budget_verified=verified==identity['arguments']['generations'])
    write_json(root/'audit.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv);print(json.dumps(audit(args.output)));return 0
if __name__=='__main__':raise SystemExit(main())
