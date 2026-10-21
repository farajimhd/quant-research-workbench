"""Read-only matched Aug-4 comparison after this local experiment completes."""
import argparse,json
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v27.local_details import metric_columns
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,write_json,require_runtime
from .composition import comparison_base


def summary(root,generation):
    folder=root/f'generation-{generation:04d}'
    seal=json.loads((folder/'complete.json').read_bytes())
    receipt=folder/'2026-08-04/receipt.json'
    if file_hash(receipt)!=seal['receipt_sha256']:raise ValueError('Comparison receipt hash changed')
    scores=json.loads((folder/'scores.json').read_bytes())
    if file_hash(folder/'scores.json')!=seal['scores_sha256']:raise ValueError('Comparison score hash changed')
    metrics=metric_columns(receipt,seal['population_sha256']);pnl=np.asarray(metrics['net_pnl']);objective=np.asarray(scores['scores']);eligible=np.asarray(scores['eligible'])
    best=int(np.argmax(np.where(eligible,objective,-np.inf)));profit=int(np.argmax(pnl))
    return dict(generation=generation,candidates=len(pnl),profitable=int((pnl>0).sum()),active=int((np.asarray(metrics['closed_positions'])>0).sum()),
        total_profit=float(pnl.sum()),mean_profit=float(pnl.mean()),median_profit=float(np.median(pnl)),best_profit=float(pnl[profit]),
        objective_winner_profit=float(pnl[best]),objective_winner_score=float(objective[best]),
        objective_winner_drawdown=float(metrics['drawdown'][best]),best_profit_drawdown=float(metrics['drawdown'][profit]),
        receipt_sha256=file_hash(receipt),complete_sha256=file_hash(folder/'complete.json'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);a=p.parse_args()
    output=require_runtime(a.output);local=output/'local/2026-08-04';baseline=a.baseline
    contracts=[json.loads((r/'checkpoint.json').read_bytes())['contract'] for r in (baseline,local)]
    for field in ('day','generations','population','seed','initial_multiplier','objective','selection','recombination','execution'):
        before=contracts[0][field];after=contracts[1][field]
        if field=='recombination':
            after=comparison_base(after,before)
        if before!=after:raise ValueError('Unmatched comparison contract: '+field)
    result=dict(v36={str(g):summary(baseline,g) for g in (1,20,50)},v39={str(g):summary(local,g) for g in (1,20,50)},
        scope='Single-session in-sample crossover/mutation comparison; no generalization conclusion',matched_fields=['population','seed','objective','selection','generations','initial_multiplier','execution'],intentional_crossover_changes={k:contracts[1]['recombination']['crossover_v39'][k] for k in ('entry_residual','changed_action_probabilities')},intentional_mutation_changes=contracts[1]['recombination']['mutation_v39'])
    for name,root in (('v36',baseline),('v39',local)):
        walls=[json.loads((root/f'generation-{g:04d}/wall.json').read_bytes())['full_generation_seconds'] for g in range(1,51)]
        result[name]['measured_generation_wall_total_seconds']=sum(walls)
    write_json(output/'comparison-v36.json',result)
    print('Matched V36/V39 Aug-4 comparison saved:',output/'comparison-v36.json',flush=True)


def compare(job):
    import sys
    sys.argv=['compare','--output',job['campaign'],'--baseline',job['baseline']]
    return main()


if __name__=='__main__':main()
