"""Run a bounded real-source market pilot before sizing a larger launch.

Uses the production graph and producers in an isolated SSD database. No source
acquisition, state bootstrap, indicator publication or V7 publication occurs.
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
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from scripts import build_market_day as builder
from pipelines.certified_research import contracts,source,recipe,universe,cohorts,independent_market,initial_plan,installation,capacity
from pipelines.certified_research.identity import identity_key,ISSUER
from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def pilot_guard(query,config,plans):
    """Finite pilot reservation, not a fabricated production pilot rate.

    Eight listing-days, eight fixed frames and two bounded rolling windows fit
    below 128 GiB even at a full 100ms grid with a 1KiB row allowance and retained
    staging/output/merge copies. Variable metadata is bounded separately by the
    graph size and existing publisher transport limits.
    """
    aggregates=[p for p in plans if p.unit.product=='market_aggregate']
    allowed={'universe','condition_rules','calculation_recipe','canonical_source',
             'market_aggregate','activity','reference_availability'}
    if (config.product_scope!='market_bars' or config.workers!=1 or len(plans)>100
            or not 1<=len(aggregates)<=8 or any(p.unit.product not in allowed for p in plans)
            or config.volume_windows_seconds!=(10,60) or len(config.resolutions_ms)>8):
        raise ValueError('Pilot exceeds its finite reservation scope')
    def check(plan_hash,current):
        if tuple(current)!=tuple(plans):raise ValueError('Pilot plan changed')
        rows=query("SELECT name,free_space,unreserved_space FROM system.disks WHERE name IN ('live_market_ssd','sip_raw_ssd')")
        disks={r['name']:r for r in rows}
        if set(disks)!={'live_market_ssd','sip_raw_ssd'}:raise ValueError('Workload SSD disks missing')
        required=128*1024**3;reserve=1024**4
        if min(min(int(r['free_space']),int(r['unreserved_space'])) for r in rows)<required+reserve:
            raise ValueError('Pilot lacks its 128 GiB reservation plus 1 TiB free reserve')
        proof=dict(status='admitted',plan_hash=plan_hash,mode='bounded-real-source-pilot-reservation-v1',
                   listing_days=len(aggregates),reservation_bytes=required,reserve_bytes=reserve,disks=rows)
        return dict(proof,certificate_hash=contracts.digest(proof))
    return check


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--runtime-root',type=Path,required=True)
    p.add_argument('--date',type=date.fromisoformat,default=date(2026,1,2))
    p.add_argument('--conid-review-hash',required=True)
    p.add_argument('--anchor-date',type=date.fromisoformat,required=True)
    a=p.parse_args(argv)
    if not a.runtime_root.is_dir():raise ValueError('Required runtime root is unavailable')
    run=a.runtime_root/('market-pilot-'+uuid4().hex[:12]);run.mkdir()
    dataset=str(uuid4());database='certified_research_pilot_'+uuid4().hex[:12]
    config=contracts.CampaignConfig(a.date,a.date,database=database,workers=1,query_threads=2,
        query_memory_bytes=2*1024**3,global_memory_bytes=2*1024**3,query_timeout_seconds=900,
        product_scope='market_bars',identity_policy='current_conid_retrospective_v1',conid_review_hash=a.conid_review_hash)
    load_env_files([a.env_file],verbose=False)
    def clients(cfg=config):
        return builder.Client(SimpleNamespace(env_file=a.env_file,max_threads=cfg.query_threads,
            max_memory_gb=cfg.query_memory_bytes/1024**3,query_timeout=cfg.query_timeout_seconds),persistent=False)
    with closing(clients()) as client,closing(open_workstation_keeper_session()) as keeper:
        canonical=source.inventory(client.query,a.date,a.date)
        reporting=source.reporting_inventory(client.query,canonical)
        epoch=source.require_ready(client.query,canonical,reporting)
        def guard():
            if source.require_ready(client.query,canonical,reporting)!=epoch:raise ValueError('Pilot source changed')
        snapshot=universe.load_for_config(client.query,a.date,a.date,a.anchor_date,config)
        selected=[i for i in snapshot['intervals'] if i.ticker in ('AAPL','NVDA','TSLA')]
        if {i.ticker for i in selected}!={'AAPL','NVDA','TSLA'}:raise ValueError('Dense pilot identities unavailable')
        config=replace(config,issuer_keys=tuple(sorted({identity_key(ISSUER,i.issuer_id) for i in selected})))
        snapshot=cohorts.select(snapshot,config.issuer_keys)
        rules=client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
        frozen=recipe.market_recipe(config,rules)
        assembled=independent_market.assemble(config,snapshot,canonical,reporting,frozen['definition'],rules,a.anchor_date,query=client.query,guard=guard)
        pilot_guard(client.query,config,assembled['plans']) # Validate scope before DDL.
        client.query('CREATE DATABASE '+database,read=False)
        with closing(DictionaryClaim(keeper.client,database)) as dictionary:installation.install(client.query,database,dictionary)
        metadata=dict(dataset_id=dataset,database=database,config=config.identity(),tickers=sorted({i.ticker for i in snapshot['intervals']}))
        (run/'pilot.json').write_text(json.dumps(metadata,indent=2,default=str))
        print('Pilot active | '+json.dumps(metadata,default=str),flush=True)
        with closing(KeeperClaim(keeper.client,dataset)) as claim:
            result=initial_plan.run(client.query,config,dataset,assembled,claim,clients,
                lambda:DictionaryClaim(keeper.client,database),lambda cfg,plans:pilot_guard(client.query,cfg,plans),
                progress=lambda event:print(json.dumps(event,default=str),flush=True))
        parts=client.query("SELECT table,sum(rows) rows,sum(bytes_on_disk) disk_bytes,sum(data_uncompressed_bytes) uncompressed_bytes FROM system.parts WHERE active AND database="+contracts.literal(database)+' GROUP BY table ORDER BY table')
        report=dict(metadata,status=result['status'],seal=result.get('seal'),errors={k:str(v) for k,v in result.get('errors',{}).items()},
                    parts=parts,demands=capacity.plan_demands(config,assembled['plans']),recipe_hash=assembled['recipe_hash'],
                    installation_hash=installation.manifest()['installation_hash'])
        (run/'result.json').write_text(json.dumps(report,indent=2,default=str))
        print('Pilot result | '+str(run/'result.json')+' | '+result['status'],flush=True)
        return 0 if result['status']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
