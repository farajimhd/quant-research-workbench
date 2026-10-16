"""Run one restart-safe market tranche of at most eight listing-days.

The 128 GiB finite reservation is an engineering allowance, not a qualified
whole-population rate or a proof of an absolute MergeTree merge peak. Recheck
the 1 TiB reserve before every tranche; retain all source and seal checks.
Indicators and historical levels remain separate products.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import json
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,timezone
from types import SimpleNamespace
from uuid import uuid4

from scripts import build_market_day as builder
from scripts.profile_certified_market import ticker_scope,pilot_guard
from pipelines.certified_research import contracts,source,universe,cohorts,recipe,independent_market
from pipelines.certified_research import initial_plan,installation,campaign,verification
from pipelines.certified_research.launch import ExecutionLaunch
from pipelines.certified_research.identity import identity_key,ISSUER
from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def request_identity(args):
    if args.start_date>args.end_date or len(source.exchange_sessions(args.start_date,args.end_date))*len(args.tickers)>8:
        raise ValueError('Tranche exceeds eight requested ticker-sessions')
    return dict(start=str(args.start_date),end=str(args.end_date),tickers=args.tickers,
                anchor=str(args.anchor_date),conid_review_hash=args.conid_review_hash,database='arte')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--start-date',type=date.fromisoformat,required=True)
    p.add_argument('--end-date',type=date.fromisoformat,required=True)
    p.add_argument('--tickers',type=ticker_scope,required=True)
    p.add_argument('--anchor-date',type=date.fromisoformat,required=True)
    p.add_argument('--conid-review-hash',required=True)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args(argv);request=request_identity(args)
    if not args.run_dir.parent.is_dir():raise ValueError('Required runtime parent is unavailable')
    if args.resume:
        metadata=json.loads((args.run_dir/'run.json').read_text())
        if metadata['request_hash']!=contracts.digest(request):raise ValueError('Resume request differs from saved tranche')
    else:
        args.run_dir.mkdir()
        metadata=dict(dataset_id=str(uuid4()),request=request,request_hash=contracts.digest(request))
        with (args.run_dir/'run.json').open('x') as f:json.dump(metadata,f,indent=2)
    dataset=metadata['dataset_id'];run=args.run_dir
    def save(value):
        value=dict(value,pid=os.getpid(),updated_at=datetime.now(timezone.utc).isoformat(),dataset_id=dataset)
        temporary=run/'status.tmp';temporary.write_text(json.dumps(value,default=str));temporary.replace(run/'status.json')
    def progress(event):
        save(dict(status='running',event=event));print(json.dumps(event,default=str),flush=True)
    config=contracts.CampaignConfig(args.start_date,args.end_date,workers=1,query_threads=2,
        query_memory_bytes=2*1024**3,global_memory_bytes=2*1024**3,query_timeout_seconds=900,
        product_scope='market_bars',identity_policy='current_conid_retrospective_v1',conid_review_hash=args.conid_review_hash)
    load_env_files([args.env_file],verbose=False)
    def clients(cfg=config):
        return builder.Client(SimpleNamespace(env_file=args.env_file,max_threads=cfg.query_threads,
            max_memory_gb=cfg.query_memory_bytes/1024**3,query_timeout=cfg.query_timeout_seconds),persistent=True)
    save(dict(status='preparing',scope=request,admission='finite-128GiB-reservation',deferred=('technical','v7')))
    try:
        with closing(clients()) as client,closing(open_workstation_keeper_session()) as keeper:
            installation.verify(client.query,'arte',require_tables=True)
            present=client.query('SELECT count() n FROM arte.research_dataset_v1 WHERE dataset_id=toUUID('+contracts.literal(dataset)+')')[0]['n']
            with closing(KeeperClaim(keeper.client,dataset)) as claim:
                dictionary=lambda:DictionaryClaim(keeper.client,'arte')
                if present:
                    saved=campaign.load_plan(client.query,'arte',dataset)
                    if saved.config.start!=args.start_date or saved.config.end!=args.end_date:
                        raise ValueError('Stored tranche dates differ')
                    guard=verification.source_guard(client.query,saved)
                    launch=ExecutionLaunch.from_stored(client.query,'arte',dataset,claim,lambda:clients(saved.config),guard,
                        pilot_guard(client.query,saved.config,saved.units),dictionary)
                    result=launch.run(stop=lambda:(run/'stop.request').exists(),progress=progress)
                else:
                    canonical=source.inventory(client.query,args.start_date,args.end_date)
                    reporting=source.reporting_inventory(client.query,canonical)
                    epoch=source.require_ready(client.query,canonical,reporting)
                    def guard():
                        if source.require_ready(client.query,canonical,reporting)!=epoch:raise ValueError('Tranche source changed')
                    snapshot=universe.load_for_config(client.query,args.start_date,args.end_date,args.anchor_date,config)
                    selected=[i for i in snapshot['intervals'] if i.ticker in args.tickers]
                    if {i.ticker for i in selected}!=set(args.tickers):raise ValueError('Requested tranche identities unavailable')
                    config=replace(config,issuer_keys=tuple(sorted({identity_key(ISSUER,i.issuer_id) for i in selected})))
                    snapshot=cohorts.select(snapshot,config.issuer_keys)
                    rules=client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
                    frozen=recipe.market_recipe(config,rules)
                    assembled=independent_market.assemble(config,snapshot,canonical,reporting,frozen['definition'],rules,args.anchor_date,query=client.query,guard=guard)
                    pilot_guard(client.query,config,assembled['plans'])
                    result=initial_plan.run(client.query,config,dataset,assembled,claim,clients,dictionary,
                        lambda cfg,plans:pilot_guard(client.query,cfg,plans),stop=lambda:(run/'stop.request').exists(),progress=progress)
        summary=dict(status=result['status'],seal=result.get('seal'),errors={k:str(v) for k,v in result.get('errors',{}).items()},
            requested_scope=request,combined_dataset_complete=False,deferred=('technical','v7'))
        (run/'result.json').write_text(json.dumps(summary,indent=2,default=str));save(summary)
        print('Market tranche | '+result['status']+' | '+dataset,flush=True)
        return 0 if result['status']=='complete' else 2
    except BaseException as error:
        save(dict(status='failed',error_type=type(error).__name__));raise


if __name__=='__main__':raise SystemExit(main())
