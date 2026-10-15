"""Read-only saved campaign verification with fresh canonical source bindings."""
from datetime import date

from . import campaign,source,runtime_plan
from .launch import ExecutionLaunch
from .contracts import literal


def source_guard(query,saved):
    expected={}
    for plan in saved.units:
        if plan.unit.product!='canonical_source':continue
        args=plan.arguments
        if set(args)!={'day','source_hash'}:raise ValueError('Stored canonical source fields differ')
        day=date.fromisoformat(args['day'])
        if day in expected:raise ValueError('Stored canonical day bindings are duplicated')
        expected[day]=args['source_hash']
    if not expected:raise ValueError('Saved verification requires explicit canonical/reporting day bindings')
    days=tuple(sorted(expected))
    def guard():
        canonical=source.inventory(query,days[0],days[-1],sessions=days)
        reporting=source.reporting_inventory(query,canonical)
        actual=source.market_day_hashes(canonical,reporting)
        if actual!=expected:raise ValueError('Saved canonical/reporting day bindings changed')
        epoch=source.require_ready(query,canonical,reporting)
        if epoch!=saved.source_hash:raise ValueError('Saved canonical/reporting source epoch changed')
    return guard


def verify(query,database,dataset_id,claim,client_factory,dictionary_claim_factory):
    """Factories receive frozen resource configuration; no producers are run."""
    saved=campaign.load_plan(query,database,dataset_id)
    guard=source_guard(query,saved)
    clients=lambda:client_factory(saved.config)
    runtime=runtime_plan.runtime(saved,claim,clients,guard)
    launch=ExecutionLaunch(query,saved.config,saved.dataset_id,saved.units,saved.source_hash,claim,clients,guard,
        lambda *args:None,dictionary_claim_factory,runtime)
    root=launch.verify()
    seals=query(f'SELECT plan_hash,source_hash,root_hash,units FROM {database}.research_campaign_seal_v1 '
        f'WHERE dataset_id=toUUID({literal(dataset_id)})')
    if (len(seals)!=1 or seals[0]['plan_hash']!=saved.plan_hash or seals[0]['source_hash']!=saved.source_hash
            or seals[0]['root_hash']!=root or int(seals[0]['units'])!=len(saved.units)):
        raise ValueError('Published campaign seal is missing, duplicated or differs from verified authority')
    return dict(status='verified',dataset_id=dataset_id,plan_hash=saved.plan_hash,source_hash=saved.source_hash,
        root_hash=root,units=len(saved.units),read_only=True,product_scope=saved.config.product_scope,
        combined_dataset_complete=saved.config.product_scope=='full')
