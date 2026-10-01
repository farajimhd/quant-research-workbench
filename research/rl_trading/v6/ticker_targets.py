"""Bind existing audited episode outcomes to independent ticker heads.

No source file is rewritten. Allocation shares are intentionally excluded.
Unavailable values/brackets remain None and are counted, never zero-filled.
"""
from dataclasses import replace
import json
from pathlib import Path
import polars as pl
from research.rl_trading.v1.common import file_hash

VERSION='rl-v6-ticker-target-bindings-v1'


def attach_targets(decisions,session,bracket_root,*,fee_per_share=.005):
    bank=Path(session.root);cert=json.loads((bank/'complete.json').read_text())
    candidates=bank/'candidates.parquet'
    if file_hash(candidates)!=cert['outputs']['candidates']['sha256']:
        raise ValueError('Ticker value source changed')
    frame=pl.read_parquet(candidates).filter(pl.col('direction')==1)
    if frame.select('episode_uid','time_us').n_unique()!=frame.height:
        raise ValueError('Ambiguous ticker value target')
    # Un-discounted fee-adjusted per-share outcome divided by entry price;
    # unlike netbps allocation scores this value has no probe-capital sizing.
    values={(r['episode_uid'],r['time_us']):r['net_value_per_share']/r['decision_close']*10000
        for r in frame.select('episode_uid','time_us','net_value_per_share','decision_close').iter_rows(named=True)}
    exits={(r['episode_uid'],r['time_us']):(r['exit_hint_us'],r['exit_hint_close'])
        for r in frame.select('episode_uid','time_us','exit_hint_us','exit_hint_close').iter_rows(named=True)}
    root=Path(bracket_root)/str(session.day);proof=json.loads((root/'complete.json').read_text())
    path=root/'oracle_brackets.parquet'
    from research.rl_trading.v6.build_price_action_brackets import VERSION as BRACKET_VERSION
    if proof.get('version')!=BRACKET_VERSION or file_hash(path)!=proof['brackets_sha256'] or proof.get('day')!=str(session.day):
        raise ValueError('Ticker bracket source certificate changed')
    brackets=pl.read_parquet(path)
    if brackets['episode_uid'].n_unique()!=brackets.height:
        raise ValueError('Ambiguous ticker bracket identity')
    bounds={r['episode_uid']:r for r in brackets.filter(pl.col('label_available')).iter_rows(named=True)}
    first={}
    for item in decisions:
        if not len(item.held_index) and item.soft_probabilities[1]>0:
            first[item.episode_uid]=min(first.get(item.episode_uid,item.close_us),item.close_us)
    result=[]
    for item in decisions:
        value=stop=target=None
        if not len(item.held_index):
            probability=item.soft_probabilities[1]
            if probability>=.5:
                value=values.get((item.episode_uid,item.close_us))
                label=bounds.get(item.episode_uid)
                # Existing oracle geometry is tied to its exact entry time.
                # Never reuse its extrema on a different entry window.
                if label is not None and label['entry_us']==item.close_us:
                    price=label['entry_price']
                    stop=(1-label['oracle_stop']/price)*10000
                    target=(label['oracle_target']/price-1)*10000
                    if not 0<stop<10000 or not target>0:raise ValueError('Invalid certified bracket distances')
        else:
            exit_=exits.get((item.episode_uid,first.get(item.episode_uid)))
            if exit_ is not None and item.close_us<exit_[0]:
                mark=float(item.held_features[0,1])*(1+float(item.held_features[0,3]))
                value=(exit_[1]-mark-2*fee_per_share)/mark*10000
        result.append(replace(item,size_fraction=None,opportunity_value_bps=value,
            entry_stop_bps=stop,entry_target_bps=target))
    counts={name:sum(getattr(i,field) is not None for i in result) for name,field in
        (('value','opportunity_value_bps'),('stop','entry_stop_bps'),('target','entry_target_bps'))}
    return tuple(result),dict(version=VERSION,bank_candidate_sha256=file_hash(candidates),
        bracket_certificate_sha256=file_hash(root/'complete.json'),counts=counts,
        value_units='net_bps',flat_value='fee_adjusted_candidate_return_without_time_discount',
        held_value='remaining_return_to_original_candidate_exit_before_exit_clock',
        missing_target='masked_not_zero',teacher_sizing=False)
