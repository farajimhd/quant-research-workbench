"""Bounded optional outlier traces; never imported by the optimizer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from .data import SessionData
from .io_pipeline import prepare_host,TransferStaging
from .evaluator import PopulationPrograms,replay_cohort,Execution
from .run_search import bind_dataset
from research.vectorized_backtest.v16.genome import Individual as LegacyIndividual
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash,configure_caches


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=require_runtime(a.output);configure_caches(out/'cache');torch.set_num_threads(1)
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    complete=[p for p in sorted((a.campaign/'training').glob('generation-*')) if (p/'complete.json').exists() and (a.campaign/'validation'/p.name/'complete.json').exists()]
    if not complete:raise ValueError('Sealed training and display-only validation required')
    train=complete[-1];valid=a.campaign/'validation'/train.name
    rows=read(train/'ranking.json');observations=read(valid/'ranking.json');population=read(train/'population.json')
    byserial={r['serial']:r for r in rows}
    with np.load(train/'session-panel.npz',allow_pickle=False) as z:
        profits=z['profits'].copy();values=z['scores'].copy();active=z['active'].copy()
    champions=set(np.argmax(np.where(active,values,-np.inf),axis=1).tolist())
    worst=min((r for r in rows if r['candidate']-1 in champions),key=lambda r:r['metrics']['total_pnl'])
    targets=[('training',max(rows,key=lambda r:r['score']),'global_leader'),('training',worst,'destructive_local_champion'),
             ('validation',max(observations,key=lambda r:r['metrics']['total_pnl']),'display_outlier')]
    summaries=[]
    for role,row,label in targets:
        original=byserial[row['serial']];member=LegacyIndividual.restore(population['population'][original['candidate']-1])
        dataset=bind_dataset(a.campaign/(role+'.json'),role);folder=a.campaign/role/train.name
        seal=read(folder/'complete.json');index=row['candidate']-1
        with np.load(folder/'session-panel.npz',allow_pickle=False) as z:day=str(z['days'][np.argmax(z['profits'][:,index])])
        saved_path=folder/day/'receipt.json';assert file_hash(saved_path)==seal['session_receipts'][day]
        saved=read(saved_path);offset=dataset['days'].index(day);cohort=dataset['days'][offset//2*2:offset//2*2+2]
        width=max(len(read(folder/d/'receipt.json')['listing_ids']) for d in cohort)
        data=prepare_host(SessionData(Path(dataset['inputs'])/day,Path(dataset['history'])/day,
            feature_cache=Path(dataset['feature_cache'])/day if dataset.get('feature_cache') else None,indicator_root=Path(dataset['indicator_root'])/day),resident=True)
        staging=TransferStaging();plan=PopulationPrograms([member],'cuda')
        try:
            staging.activate([(day,data)],torch.device('cuda'));gates=plan.evaluate(data,maximum_gate_gib=8.)
            result=replay_cohort([data],[member],[gates],execution=Execution(**saved['execution']),backend='compile',listing_width=width,trace=True)[0]
            exact=float(result['net_pnl'][0])==saved['metrics']['net_pnl'][index]
            if not exact:raise ValueError('Outlier trace differs from saved financial receipt; preserve evidence and investigate')
            trades=[]
            for profit,j,begin,end in zip(result['episode_profits'][0],result['episode_listings'][0],result['episode_entry_clocks'][0],result['episode_exit_clocks'][0]):
                identity=data.receipt['listings'][int(data.listing_ids[j])]
                assert 0<begin<=end<data.clocks
                assert bool(data.host_tensors['observed'][begin,j])
                assert bool(data.host_tensors['observed'][end,j]) or end==data.clocks-1
                trades.append(dict(listing=identity,net_profit=profit,entry_clock_index=begin,exit_clock_index=end,
                    entry_epoch_second=int(data.arrays['clocks'][begin]),exit_epoch_second=int(data.arrays['clocks'][end]),
                    initial_entry_mark=float(data.host_tensors['mark'][begin,j]),exit_mark=float(data.host_tensors['mark'][end,j]),
                    entry_signal_bits=int(gates[0,begin-1,j]),exit_observed=bool(data.host_tensors['observed'][end,j]),
                    terminal_forced_liquidation=end==data.clocks-1,execution=saved['execution']))
            record=dict(label=label,split=role,generation=train.name,candidate_id=row['candidate_id'],day=day,
                receipt_sha256=file_hash(saved_path),input_identity=data.identity,exact_saved_profit=True,net_profit=float(result['net_pnl'][0]),trades=trades,
                scope='Certified input marks, initial entry and final exit clocks, complete-position net profits; intermediate adds/reductions remain represented in net accounting')
            write_json(out/(label+'.json'),record);summaries.append({k:v for k,v in record.items() if k!='trades'})
        finally:plan.close();staging.close();data.close();torch.cuda.empty_cache()
    write_json(out/'audit.json',dict(status='passed',targets=summaries,validation_feedback=False))


if __name__=='__main__':main()
