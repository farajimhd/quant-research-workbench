"""Deterministic receipt/objective audit; never opens unscored holdout inputs."""
import argparse,json
from pathlib import Path
import torch
from .runtime import file_hash,write_json
from .run_search import restore,state,fingerprint,clean
from .stability import Objective,score
from .feature_bank import CATALOG
from .financial_audit import audit_fills

def audit(root):
    root=Path(root);identity=json.loads((root/'identity.json').read_text());objective=Objective(**identity['objective'])
    initial_cash=identity['financial_settings']['initial_cash']
    verified=0;financial_verified=0;best=None;winner=None;generations=[]
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
            audit_fills(path.parent/'fills.pt',r['metrics'],initial_cash=initial_cash)
            financial_verified+=1
        if dates!=[s['day'] for s in identity['sessions']['training']]:raise ValueError('Generation does not cover exact all30 training dates')
        def tensor(name):return torch.tensor([r[name] for r in results],dtype=torch.float64)
        complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in v.programs().values()) for v in population],dtype=torch.float64)
        calculated=score(tensor('net_pnl'),tensor('drawdown'),tensor('stop_risk_dollar_seconds'),tensor('capital_dollar_seconds'),tensor('filled_batches'),tensor('terminal_valid'),complexity,config=objective)
        original=generation['scores']
        torch.testing.assert_close(calculated['score'],torch.tensor(original['score'],dtype=torch.float64),rtol=1e-12,atol=1e-12)
        if calculated['feasible'].tolist()!=original['feasible']:raise ValueError('Feasibility arithmetic changed')
        for lane,feasible in enumerate(calculated['feasible'].tolist()):
            value=float(calculated['score'][lane])
            if feasible and (best is None or value>best):best=value;winner=state(population[lane])
        generations.append(dict(path=str(folder/'generation.json'),sha256=file_hash(folder/'generation.json')))
        verified+=1
    freeze=root/'frozen_winner.json';validation=0
    if freeze.exists():
        frozen=json.loads(freeze.read_text());h=file_hash(freeze)
        if frozen['identity_sha256']!=file_hash(root/'identity.json'):raise ValueError('Frozen winner identity changed')
        checkpoint=json.loads((root/'checkpoint.json').read_text())
        if (verified!=identity['arguments']['generations'] or checkpoint['next_generation']!=verified
                or frozen['winner']!=winner or checkpoint['winner']!=winner
                or frozen['criterion']!=identity['objective'] or frozen['score']!=best
                or checkpoint['best_score']!=best):
            raise ValueError('Frozen winner is not the exact full-budget best feasible candidate')
        for folder in sorted(root.glob('validation_*')):
            receipt=folder/'receipt.json'
            if not receipt.exists():continue
            r=json.loads(receipt.read_text())
            if r['freeze_sha256']!=h or r['evaluation_epoch']<=frozen['frozen_epoch'] or file_hash(folder/'fills.pt')!=r['ledger_sha256']:raise ValueError('Freeze/evaluation order or ledger binding invalid')
            audit_fills(folder/'fills.pt',r['metrics'],initial_cash=initial_cash)
            financial_verified+=1
            validation+=1
    report=dict(status='passed',completed_generations_verified=verified,validation_sessions_verified=validation,financial_receipts_verified=financial_verified,identity_sha256=file_hash(root/'identity.json'),full_budget_verified=verified==identity['arguments']['generations'],
                generation_bindings=generations,freeze_sha256=file_hash(freeze) if freeze.exists() else None,
                checkpoint_sha256=file_hash(root/'checkpoint.json') if (root/'checkpoint.json').exists() else None)
    write_json(root/'audit.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv);print(json.dumps(audit(args.output)));return 0
if __name__=='__main__':raise SystemExit(main())
