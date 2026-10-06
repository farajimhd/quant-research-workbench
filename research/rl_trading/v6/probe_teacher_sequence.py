"""Bounded laptop GPU check on immutable public training labels; no final training.

Full day/prior banks and selected target bytes are verified before slicing.
The transient ticker subset is explicitly diagnostic, not a new dataset.
No broker, PPO, sealed labels, registry writes or final checkpoints are used.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
from dataclasses import asdict, replace
from datetime import date
import json
import subprocess
from pathlib import Path
import time

import numpy as np
import torch

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.opportunity_dataset import load_teacher
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import PackedSession, open_session
from research.rl_trading.v6.teacher_forecast import configure, CONTRACT
from research.rl_trading.v6.training import train_session


def subset(session, labels, identities, begin, end, *, valid_price_context=False):
    """Preserve original features; rekey only this declared diagnostic axis."""
    identities=tuple(sorted(identities));original={s:i for i,s in enumerate(session.listings)}
    local={original[s]:i for i,s in enumerate(identities)}
    def pack(bank, previous=False):
        arrays=[[],[],[]];offsets={};cursor=0
        for identity in identities:
            if identity not in bank.manifest['offsets']:continue
            values=bank.listing(identity)
            high=int(np.searchsorted(values.close_us,begin)) if previous else int(np.searchsorted(values.close_us,end,side='right'))
            low=max(0,high-120) if previous else 0
            if previous and valid_price_context:
                valid=np.flatnonzero(np.asarray(values.scalar[:high])[:,35]>.5)
                # Retain one predecessor to reconstruct the first elapsed gap.
                low=int(valid[-121]) if len(valid)>=121 else 0
            offsets[identity]=[cursor,cursor+high-low];cursor+=high-low
            for target,source in zip(arrays,(values.close_us,values.scalar,values.levels)):
                target.append(np.array(source[low:high],copy=True))
        return SessionBank(Path('diagnostic-memory'),dict(offsets=offsets),*(np.concatenate(a) for a in arrays))
    bank=pack(session.bank)
    # Current session warmup before begin remains in bank and is consumed by
    # train_session; prior context remains the actual preceding session tail.
    previous=pack(session.previous,True) if session.previous is not None else None
    selected=[];n=len(identities);source_n=len(session.listings)
    for item in labels:
        if not begin<=item.close_us<=end:continue
        old=int(item.held_index[0]) if len(item.held_index) else item.soft_tokens[1]-1
        if old not in local:continue
        i=local[old];held=bool(len(item.held_index))
        token=(1+n if item.token==1+source_n else 1+n+3) if held else (1+i if item.token else 0)
        soft=(1+n,1+n+3) if held else (0,1+i)
        enter=np.zeros(n,bool)
        if not held:enter[i]=bool(item.enter_allowed[old])
        selected.append(replace(item,token=token,soft_tokens=soft,
            held_index=np.array([i],np.int64) if held else np.empty(0,np.int64),enter_allowed=enter))
    selected.sort(key=lambda d:(d.close_us,d.episode_uid,len(d.held_index)))
    ordered=[];last=None;order=0
    for item in selected:
        if item.close_us!=last:last=item.close_us;order=0
        ordered.append(replace(item,order_index=order));order+=1
    return PackedSession(session.day,session.role,session.root,session.source_certificate_sha256,
                         bank,previous,identities,session.context_split_receipt_sha256),tuple(ordered)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-runtime',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--day',default='2026-07-31')
    parser.add_argument('--tickers',nargs='+',default=['AAPL','NVDA'])
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--epochs',type=int,default=3)
    args=parser.parse_args(argv)
    output=args.output.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or not 1<=args.seconds<=120 or not 1<=args.epochs<=5 or not 1<=len(args.tickers)<=3:
        raise ValueError('Probe requires bounded laptop runtime output, <=120 seconds, <=5 epochs and <=3 tickers')
    if not torch.cuda.is_available():raise ValueError('Laptop GPU is required for this probe')
    if (output/'complete.json').exists():raise ValueError('Completed probe is immutable; use a new output directory')
    output.mkdir(parents=True,exist_ok=True)
    from research.rl_trading.v6 import saved_label_audit as source, published_market_audit as market
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME']=str(args.source_runtime.resolve())
    start=time.perf_counter()
    active,entry,proof,root,bankroot,_=source.session(args.day)
    if entry['role']!='train':raise ValueError('Probe permits public train days only, never development/sealed optimization')
    names=source.saved_symbols(str(bankroot),entry['bank_certificate_sha256'])
    identities=sorted(i for i,ticker in names.items() if ticker in args.tickers)
    if len(identities)!=len(args.tickers):raise ValueError('Tickers must resolve uniquely in certified population')
    print('Verifying complete current/prior feature bank bytes (CPU only)',flush=True)
    full=open_session(bankroot,runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']))
    ma,_,me,mroot,mp=market.session(args.day)
    print('Verifying original day and selected copied 1b target shards',flush=True)
    labels,_=load_teacher(root,full,runtime_root=source.runtime(),audit_development=True,
                         audit_listing_ids=identities,market_root=mroot)
    eligible=[d.close_us for d in labels if d.allocation_ratio_target is not None]
    if not eligible:raise ValueError('No selected ENTRY sizing targets in probe tickers')
    begin=min(eligible);end=begin+args.seconds*1_000_000
    session,labels=subset(full,labels,identities,begin,end)
    del full
    print(f'Bounded training: {len(labels)} decisions, {sum(d.allocation_ratio_target is not None for d in labels)} allocation targets',flush=True)
    torch.manual_seed(17);device=torch.device('cuda');torch.set_num_threads(4)
    policy=configure(RankedBracketActorCritic(config=MarketAttentionConfig(**source.published()[1]['ranking']),wait_hold=True).to(device))
    policy.independent_episode_supervision=True
    optimizer=torch.optim.Adam(policy.parameters(),lr=3e-4)
    initial={k:v.detach().clone() for k,v in policy.state_dict().items()}
    development=replace(session,role='development')  # Evaluate this train slice, explicitly below.
    before=train_session(policy,None,development,labels,(),device=device,evaluation=True)
    records=[]
    for epoch in range(args.epochs):
        trained=train_session(policy,optimizer,session,labels,(),device=device,clocks_per_chunk=16,teacher_loss='balanced-v2')
        evaluated=train_session(policy,None,development,labels,(),device=device,evaluation=True)
        records.append(dict(epoch=epoch+1,training=asdict(trained),train_slice_evaluation=asdict(evaluated)))
        print(json.dumps(dict(epoch=epoch+1,allocation_mae=evaluated.allocation_ratio_mae,forecast_ce=evaluated.forecast_cross_entropy)),flush=True)
    changed={k:not torch.equal(initial[k],policy.state_dict()[k]) for k in
             ('encoder.project.weight','action_gru.weight_ih','action_gru.weight_hh','decoder.size_head.weight')}
    if not all(changed.values()):raise ValueError('Required supervised parameters did not update')
    checkpoint=output/'diagnostic.pt'
    torch.save(dict(model=policy.state_dict(),optimizer=optimizer.state_dict(),contract=CONTRACT),checkpoint)
    saved=torch.load(checkpoint,map_location=device,weights_only=True)
    restored=configure(RankedBracketActorCritic(config=policy.ranking_config,wait_hold=True).to(device))
    restored.independent_episode_supervision=True;restored.load_state_dict(saved['model'])
    reload=train_session(restored,None,development,labels,(),device=device,evaluation=True)
    if asdict(reload)!=records[-1]['train_slice_evaluation']:raise ValueError('Checkpoint reload changed evaluation')
    result=dict(status='passed',contract=CONTRACT,device=torch.cuda.get_device_name(),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_files_sha256={name:file_hash(Path(__file__).parent/name) for name in
            ('teacher_forecast.py','ticker_heads.py','training.py','ranked_policy.py','opportunity_dataset.py','probe_teacher_sequence.py')},
        dataset_sha256=active['sha256'],market_dataset_sha256=ma['sha256'],day=args.day,
        source_role='train',evaluation_scope='same_training_slice_not_development_or_performance',
        bank_certificate_sha256=entry['bank_certificate_sha256'],teacher_certificate_sha256=entry['teacher_sha256'],
        market_certificate_sha256=me['sha256'],identities=identities,begin_us=begin,end_us=end,
        decisions=len(labels),before=asdict(before),epochs=records,changed_parameters=changed,
        checkpoint_sha256=file_hash(checkpoint),reload_exact=True,sealed_labels_read=False,
        final_training_started=False,workstation_gpu_used=False,elapsed_seconds=time.perf_counter()-start)
    result['hash']=digest(result)
    (output/'complete.json').write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],output=str(output),elapsed_seconds=result['elapsed_seconds'])),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
