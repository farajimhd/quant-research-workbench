"""Sealed generation-ten risk-factor comparison; read-only metrics."""
import json
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,write_json


def compare(job):
    result=dict(generation=10,scope='Aug 4 in-sample risk ablation; no generalization claim',versions={})
    for version in ('v34','v35','v36','v37','v38'):
        root=Path('D:/TradingML/runtimes/vectorized_backtest')/('torch_backtest_'+version)
        pointer=root/'active-campaign.json'
        if not pointer.exists():continue
        local=Path(json.loads(pointer.read_bytes())['campaign'])/'local/2026-08-04';folder=local/'generation-0010'
        if not (folder/'complete.json').exists():continue
        seal=json.loads((folder/'complete.json').read_bytes());receipt=folder/'2026-08-04/receipt.json';scores=folder/'scores.json'
        if file_hash(receipt)!=seal['receipt_sha256'] or file_hash(scores)!=seal['scores_sha256']:raise ValueError('Comparison seal changed')
        # The terminal reader projects display columns and deliberately omits
        # risk-rejection counters. Reports require the sealed receipt itself.
        financial=json.loads(receipt.read_bytes())
        if financial['population_sha256']!=seal['population_sha256']:raise ValueError('Comparison population identity changed')
        m=financial['metrics'];s=json.loads(scores.read_bytes());pnl=np.asarray(m['net_pnl']);eligible=np.asarray(s['eligible']);values=np.asarray(s['scores'])
        if any(len(m[k])!=len(pnl) for k in ('risk_rejected_entries','risk_rejected_adds')) or len(eligible)!=len(pnl) or len(values)!=len(pnl):raise ValueError('Comparison candidate alignment changed')
        contract=json.loads((local/'checkpoint.json').read_bytes())['contract']
        for key in ('population','seed','initial_multiplier','objective','selection'):
            if contract[key]!=job['comparison_contract'][key]:raise ValueError('Unmatched comparison: '+key)
        i=int(np.argmax(np.where(eligible,values,-np.inf)))
        result['versions'][version]=dict(candidates=len(pnl),eligible=int(eligible.sum()),profitable=int((pnl>0).sum()),median_profit=float(np.median(pnl)),mean_profit=float(pnl.mean()),best_profit=float(pnl.max()),top20_profit_mean=float(np.sort(pnl)[-20:].mean()),
            objective_winner={k:m[k][i] for k in ('net_pnl','drawdown','maximum_position_risk','maximum_stop_distance','closed_positions','winning_positions')},best_objective=float(values[i]),
            rejected_entries=float(np.sum(m['risk_rejected_entries'])),rejected_adds=float(np.sum(m['risk_rejected_adds'])),execution=contract['execution'],crossover='v36' if version=='v36' else 'v35-baseline',
            receipt_sha256=file_hash(receipt),seal_sha256=file_hash(folder/'complete.json'))
    write_json(Path(job['root'])/'comparison-generation10.json',result)
    print(json.dumps(result,indent=2),flush=True)
