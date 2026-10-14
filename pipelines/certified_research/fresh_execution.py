"""First launch using the same certified plan runtime as stored resume."""
from datetime import date
from threading import Event

from . import context,initial_preparation,initial_plan,recipe,source


def run(query,config,dataset_id,claim,client_factory,dictionary_claim_factory,capacity_factory,*,
        context_start,fundamental_start,anchor_date,stop=None,progress=None,rebuild_origin=None):
    """Prepare retained inputs, then admit and execute the complete graph.

    Missing reference authorities and compatible initial states remain explicit
    blockers unless the caller declares a versioned rebuild origin. No partial
    plan or unrequested empty-state reset is created.
    Canonical acquisition/repair is performed by the launcher before this call.
    """
    stop=Event() if stop is None else stop
    if type(context_start) is not date or context_start>config.start:
        raise ValueError('Fresh execution requires an explicit earlier source context bound')
    targets=source.exchange_sessions(config.start,config.end)
    if not targets:raise ValueError('Fresh execution has no exchange sessions')
    if rebuild_origin is not None and (type(rebuild_origin) is not date or not context_start<=rebuild_origin<=targets[0]
            or not source.exchange_sessions(rebuild_origin,rebuild_origin)):
        raise ValueError('Explicit rebuild origin must be an exchange session within the frozen context/target bounds')
    claim.check()
    canonical=source.inventory(query,context_start,config.end)
    reporting=source.reporting_inventory(query,canonical)
    epoch=source.require_ready(query,canonical,reporting)
    def guard():
        if stop.is_set():raise InterruptedError('Fresh launch stopped')
        claim.check()
        if source.require_ready(query,canonical,reporting)!=epoch:
            raise ValueError('Fresh launch canonical source changed')
    rules=query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume "
        "FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' "
        'AND is_join_canonical=1 ORDER BY token_id')
    frozen=recipe.market_recipe(config,rules)
    short_days=context.preceding_sessions(targets[0],max(config.short_windows_sessions))
    assembled=initial_preparation.discover_and_prepare(query,config,dataset_id,claim,lambda:client_factory(config),
        guard,canonical,reporting,frozen['definition'],rules,fundamental_start=fundamental_start,
        short_start=min((*short_days,*targets)),anchor_date=anchor_date,stop=stop,progress=progress,rebuild_origin=rebuild_origin)
    if not assembled['ready']:
        missing=assembled['missing_authorities']
        return dict(status='blocked',errors={'reference-authorities':ValueError(f'{len(missing)} required reference authority scopes are missing')},
            seal=None,missing_authorities=missing)
    return initial_plan.run(query,config,dataset_id,assembled,claim,client_factory,dictionary_claim_factory,
        capacity_factory,stop=stop,progress=progress)
