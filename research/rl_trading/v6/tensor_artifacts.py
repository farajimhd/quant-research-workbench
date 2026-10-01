"""Export compact approximate-broker GPU evidence once after collection."""
import json
from pathlib import Path
import polars as pl
import torch
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.tensor_broker import VERSION


def save_tensor_replay(collection,session,checkpoint,*,runtime_root,source_commit,
                       quote_evidence_certificate):
    runtime=Path(runtime_root).resolve();checkpoint=Path(checkpoint).resolve()
    if not checkpoint.is_relative_to(runtime):raise ValueError('Checkpoint outside runtime')
    token=digest((VERSION,file_hash(checkpoint),session.source_certificate_sha256,
                  file_hash(quote_evidence_certificate)))[:20]
    root=runtime/'rl-v6-tensor-replays'/str(session.day)/token;root.mkdir(parents=True,exist_ok=True)
    if (root/'complete.json').exists():
        raise ValueError('Replay evidence already exists; never silently overwrite')
    groups=[f.outcomes for f in collection.frames if f.outcomes.listing.numel()]
    groups += [s.immediate_outcome for s in collection.steps
               if s.immediate_outcome is not None and s.immediate_outcome.listing.numel()]
    fields={'listing':'listing','action':'action','shares':'shares','price':'price',
            'fee':'fee','clock_us':'clock','net_pnl':'net_pnl','position_closed':'position_closed'}
    if groups:
        data={name:torch.cat([getattr(g,attribute) for g in groups]).cpu().numpy()
              for name,attribute in fields.items()}
        # Filled events precede the policy's bracket proposal at equal clocks.
        orders=pl.DataFrame(data).with_columns((pl.col('action')>=3).alias('_proposal')).sort(
            'clock_us','_proposal','listing',maintain_order=True).drop('_proposal')
    else:
        orders=pl.DataFrame(schema={'listing':pl.Int64,'action':pl.Int64,'shares':pl.Int64,
            'price':pl.Float64,'fee':pl.Float64,'clock_us':pl.Int64,
            'net_pnl':pl.Float64,'position_closed':pl.Boolean})
    orders.write_parquet(root/'orders.parquet')
    # Each closing event ends the identity's current position. This post-rollout
    # reduction does not execute orders or change the GPU account state.
    fills=orders.filter(pl.col('shares')>0).with_columns(
        (pl.col('position_closed').cast(pl.Int64).cum_sum().over('listing')-
         pl.col('position_closed').cast(pl.Int64)).alias('position_id'))
    positions=fills.group_by('listing','position_id',maintain_order=True).agg(
        pl.col('clock_us').min().alias('first_fill_us'),pl.col('clock_us').max().alias('last_fill_us'),
        pl.col('net_pnl').sum(),pl.col('fee').sum(),pl.col('position_closed').any().alias('closed'))
    positions.write_parquet(root/'positions.parquet')
    equity=collection.equity.cpu().numpy()
    pl.DataFrame({'clock_us':collection.clocks,'equity':equity}).write_parquet(root/'equity.parquet')
    peak=torch.cummax(collection.equity,0).values
    summary={**collection.summary,'maximum_drawdown_dollars':float((peak-collection.equity).max().cpu())}
    (root/'metrics.json').write_text(json.dumps(summary,sort_keys=True))
    certificate={'status':'complete','environment_version':VERSION,'source_commit':source_commit,
        'checkpoint_sha256':file_hash(checkpoint),'bank_certificate':session.source_certificate_sha256,
        'quote_certificate_sha256':file_hash(quote_evidence_certificate),'day':str(session.day),
        'decision_cadence':'one_proposal_per_second','fill_scenario':summary['fill_scenario'],
        'files':{n:file_hash(root/n) for n in ('orders.parquet','positions.parquet','equity.parquet','metrics.json')}}
    (root/'complete.json').write_text(json.dumps(certificate,sort_keys=True))
    return root,summary
