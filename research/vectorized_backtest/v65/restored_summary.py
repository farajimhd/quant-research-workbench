"""Process-local receipt restoration with hash-bound, pickle-free founder summaries."""
import json,time
from pathlib import Path
from uuid import uuid4
import numpy as np
from .completed_replays import restore,reference
from .distributed_assets import sha
from .publication import write_json
from .objectives import Objective
from .full_common import prepare_summary


def location(out,generation,day):
    return Path(out)/'restored-summary'/f'generation-{generation:04d}'/day/'summary.json'


def authority(job,receipt_hash,token,population):
    return dict(version=2,source=job['source_sha256'],receipt_sha256=receipt_hash,
                token=token,population=population,objective=job['objective'],execution=job['execution'])


def load(path,expected):
    path=Path(path)
    if not path.exists():return None
    record=json.loads(path.read_bytes())
    if record['authority']!=expected:raise ValueError('Restored summary authority changed')
    arrays=path.with_name(record['arrays'])
    if sha(arrays)!=record['sha256']:raise ValueError('Restored summary arrays changed')
    with np.load(arrays,allow_pickle=False) as bank:
        metrics={k:bank[k] for k in ('net_pnl','closed_positions','terminal_valid')}
        metrics['_session_panel']={k[6:]:bank[k] for k in bank.files if k.startswith('panel:')}
        metrics['_founder_descriptor']=(bank['entries'],bank['exposure'])
        metrics['_behavior_sketch']=(bank['behavior'],bank['active'])
        metrics['selection_behavior_keys']=[row.tobytes() for row in bank['behavior_keys']]
        metrics['ticker_score']=json.loads(bank['ticker_scores'].tobytes())
    objective=expected['objective']
    metrics['_summary_objective']=(objective['drawdown_weight'],objective['loss_weight'])
    return metrics


def save(path,metrics,expected):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    arrays=path.with_name('arrays-'+uuid4().hex+'.npz')
    values={k:metrics[k] for k in ('net_pnl','closed_positions','terminal_valid')}
    values.update({'panel:'+k:v for k,v in metrics['_session_panel'].items()})
    values.update(entries=metrics['_founder_descriptor'][0],exposure=metrics['_founder_descriptor'][1])
    values.update(behavior=metrics['_behavior_sketch'][0],active=metrics['_behavior_sketch'][1],
                  behavior_keys=np.frombuffer(b''.join(metrics['selection_behavior_keys']),np.uint8).reshape(-1,32),
                  ticker_scores=np.frombuffer(json.dumps(metrics['ticker_score'],separators=(',',':'),allow_nan=False).encode(),np.uint8))
    with arrays.open('wb') as stream:np.savez(stream,**values)
    write_json(path,dict(authority=expected,arrays=arrays.name,sha256=sha(arrays)))


def prepared(job,out,role,day,generation,token,population):
    """Runs in a replay worker process, never in the controller's Python threads."""
    started=time.perf_counter();path,expected=reference(job,out,role,day,generation)
    cache=location(out,generation,day);contract=authority(job,expected,token,population)
    metrics=None
    if generation==0 and cache.exists():
        if sha(path)!=expected:raise ValueError('Completed replay continuation receipt changed')
        metrics=load(cache,contract)
    hit=metrics is not None
    if metrics is None:
        panels,refs=restore(job,out,role,[day],generation,token,population)
        metrics=panels[0]
        if generation==0:
            from .founder_selection import session_descriptor
            from .behavior_sketch import sketch
            from .behavior_selection import session_keys
            prepare_summary(metrics,Objective(**job['objective']))
            metrics['_founder_descriptor']=session_descriptor(metrics)
            metrics['_behavior_sketch']=sketch(metrics,population)
            metrics['selection_behavior_keys']=session_keys(metrics)
            # Admission and reproduction need these exact score/behavior/ticker
            # summaries, not millions of Python ledger scalars over IPC.
            metrics={k:metrics[k] for k in ('net_pnl','closed_positions','terminal_valid',
                '_session_panel','_summary_objective','_founder_descriptor','_behavior_sketch',
                'selection_behavior_keys','ticker_score')}
            save(cache,metrics,contract)
        else:
            from .controller_metrics import compact
            metrics=compact(metrics);prepare_summary(metrics,Objective(**job['objective']))
    return dict(day=day,metrics=metrics,ref=dict(receipt=str(path),receipt_sha256=expected),
                restored=True,summary_cached=hit,timings=[],wall_seconds=time.perf_counter()-started,clocks=19799)
