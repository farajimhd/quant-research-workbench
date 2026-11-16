"""Categorized validation scalars with explicit evaluation steps and local receipts."""
import json
import math
from pathlib import Path

from ..v5.shards.contract import digest,write_json
from ..v5.shards.phase1_contract import CHANNELS

FAMILIES=('price','activity')
ACTIVITY=tuple(CHANNELS[i] for i in (4,5,10,11,16,17))
GROUPS=('validation_loss','validation_full_raw_mae',
    'validation_full_price_mape_pct','validation_full_price_median_ape_pct','validation_full_price_p90_ape_pct',
    'validation_full_price_mape_pct_by_price_band','validation_full_price_median_ape_pct_by_price_band',
    'validation_full_price_p90_ape_pct_by_price_band','validation_price_coverage',
    'validation_health','validation_other')


def categorized(validation,epoch,step):
    result={'validation_optimizer_step':step,'validation_epoch':epoch}
    def put(key,value):
        if isinstance(value,dict):
            for name,child in value.items():put(key+'/'+str(name),child)
        elif isinstance(value,(list,tuple)):
            for i,child in enumerate(value):put(key+'/'+str(i),child)
        elif value is None or (isinstance(value,(int,float)) and not math.isfinite(value)):
            result['validation_health/unavailable/'+key]=1
        elif isinstance(value,(int,float,str,bool)):result[key]=value
        else:raise ValueError('Unsupported validation metric type: '+key)
    for key,value in validation.items():
        if key=='masked_objective':put('validation_loss/masked_total',value)
        elif key in ('masked_families','all_visible_families'):
            if not isinstance(value,(list,tuple)) or len(value)!=2:raise ValueError('Expected two raw loss families')
            scope='masked' if key=='masked_families' else 'all_visible'
            for family,number in zip(FAMILIES,value):put('validation_loss/'+scope+'_'+family,number)
        elif key=='full_raw_mae':put('validation_full_raw_mae',value)
        elif key=='negative_activity_predictions':
            if len(value)!=len(ACTIVITY):raise ValueError('Activity metric shape changed')
            for name,number in zip(ACTIVITY,value):put('validation_health/negative_activity/'+name,number)
        elif key=='full_price_relative_quantile_bin_relative_width':put('validation_health/price_quantile_bin_relative_width',value)
        elif key.startswith('full_price_relative_'):put('validation_price_coverage/'+key.removeprefix('full_price_relative_'),value)
        elif key.startswith('full_price_'):put('validation_'+key,value)
        elif key in ('origins','full_population','raw_nonfinite','scope'):
            put('validation_health/'+key,value)
        else:put('validation_other/'+key,value)
    return result


def configure(reporter):
    reporter.define_metric('validation_optimizer_step')
    for group in GROUPS:reporter.define_metric(group+'/*',step_metric='validation_optimizer_step',step_sync=False)


def publish(reporter,validation,epoch,step,output):
    """Append a new SDK row, plotted against the original evaluation step.

    Receipts say submitted, not remotely verified. Saved results remain the
    immutable recovery authority; no history row or metric is rewritten/deleted.
    """
    path=Path(output)/'wandb-validation-groups.json'
    journal={'version':1,'run_id':reporter.id,'submitted':{}}
    if path.exists():
        journal=json.loads(path.read_text())
        if journal.get('hash')!=digest({k:v for k,v in journal.items() if k!='hash'}) or journal['run_id']!=reporter.id:
            raise ValueError('Categorized validation receipt changed or belongs to another run')
    identity=digest(dict(epoch=epoch,step=step,validation=validation));key=str(epoch)
    if key in journal['submitted']:
        if journal['submitted'][key]['validation_hash']!=identity:raise ValueError('Completed validation changed')
        return False
    payload=categorized(validation,epoch,step)
    # Custom x-axis retains historical evaluation positions; SDK row stays open
    # and monotonic even when a resume backfills an older completed validation.
    row=max(step,reporter.step)
    reporter.log(payload,step=row,commit=True)
    journal['submitted'][key]=dict(validation_hash=identity,evaluation_step=step,reporting_row=row,
                                  scalar_count=sum(isinstance(v,(int,float,bool)) for v in payload.values()))
    journal.pop('hash',None);journal['hash']=digest(journal);write_json(path,journal)
    return True
