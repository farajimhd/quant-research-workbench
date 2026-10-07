"""Frozen default/winner evaluation once. Never selects or tunes on validation."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .runtime import require_runtime,write_json,file_hash,configure_caches,code_hash
from .staged_audit import require_frozen
from .genome import StrategySpace
from .evolution import sample,STAGES
from .program import Node
from .run_search import restore,state,clean,fingerprint
from .batched import BatchedEvaluator
from .offline_data import load_session
from .session_prefetch import SessionPrefetch
from .staged import objective_matrix
from .stability import Objective
from .metrics import financial_metrics
from .financial_audit import audit_fills


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv);root=require_runtime(a.output)
    if (root/'report.json').exists():raise ValueError('Completed validation is immutable; never repeat')
    identity,freeze=require_frozen(root);freeze_sha=file_hash(root/'frozen_winner.json')
    if identity['code_hash']!=code_hash():raise ValueError('Final evaluator requires the exact immutable simulation source')
    missing=[]
    for session in identity['sessions']['validation']:
        paths=[Path(session['execution_root'])/'receipt.json',Path(session['execution_root'])/'tape.pt',
               Path(session['feature_root'])/'complete.json',Path(session['identity_map']),Path(session['split_certificate'])]
        if session.get('previous_feature_root'):paths.extend([Path(session['previous_feature_root'])/'complete.json',Path(session['previous_split_certificate'])])
        missing.extend(dict(day=session['day'],path=str(path)) for path in paths if not path.is_file())
    if missing:write_json(root/'validation_inputs_pending.json',dict(freeze_sha256=freeze_sha,missing=missing));return 3
    args=argparse.Namespace(**identity['arguments']);args.output=root;args.batch_size=2;args.ticker_capacity=None
    configure_caches(root/'validation-compiler');torch.set_num_threads(1)
    space=StrategySpace();default=sample(np.random.default_rng(0),space,1)[0];default.policy=space.default.tolist()
    default.clauses={s:[([Node(0,feature=35) if s in ('entry','trail') else Node(1,value=0,unit='bool')],0)] for s in STAGES}
    default.connectors={s:[] for s in STAGES};population=[default,restore(freeze['winner'])]
    population_hash=fingerprint([state(v) for v in population]);evaluator=BatchedEvaluator(space,args)
    lock=root/'owner.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
    status=dict(version='V5',status='validation',stage='Frozen default/winner once',validation_status='FROZEN; evaluation active',
                started_epoch=time.time(),config=dict(population=2,training_sessions=6,generations=0),completed_sessions=0)
    def emit(**event):status.update(event,updated_epoch=time.time(),worker_pid=os.getpid());write_json(root/'status.json',clean(status))
    results=[];bindings=[]
    try:
        with SessionPrefetch(identity['sessions']['validation'],lambda s:load_session(s,isolated_banks=True)) as prefetch:
            for index,session in enumerate(identity['sessions']['validation']):
                folder=require_runtime(root/f'validation_{index:03d}')
                before=folder/'evaluation-start.json'
                if before.exists():
                    started=json.loads(before.read_text())
                    if started['freeze_sha256']!=freeze_sha or started['population_sha256']!=population_hash:raise ValueError('Frozen evaluation identity changed')
                else:write_json(before,dict(freeze_sha256=freeze_sha,population_sha256=population_hash,evaluation_epoch=time.time()))
                if json.loads(before.read_text())['evaluation_epoch']<=freeze['frozen_epoch']:raise ValueError('Validation began before frozen winner')
                emit(focus=session['day']);receipt=evaluator.evaluate(session,population,folder,prefetch.take(index),emit)
                if receipt['population_sha256']!=population_hash:raise ValueError('Validation population changed')
                for batch in receipt['batch_receipts']:
                    path=folder/batch['directory'];value=json.loads((path/'receipt.json').read_text())
                    if file_hash(path/'fills.pt')!=value['ledger_sha256']:raise ValueError('Final actual fill bytes changed')
                    audit_fills(path/'fills.pt',value['metrics'])
                results.append(receipt['metrics']);bindings.append(dict(path=str(folder/'receipt.json'),sha256=file_hash(folder/'receipt.json'),start_sha256=file_hash(before)))
                emit(completed_sessions=index+1,timing=receipt['timing'])
        scored=objective_matrix(results,population,Objective(**identity['objective']))
        from .input_audit import FreshHashes,verify_session
        hashes=FreshHashes()
        for session,binding in zip(identity['sessions']['validation'],bindings):
            verify_session(session,json.loads(Path(binding['path']).read_text()),hashes)
        write_json(root/'final_input_audit.json',dict(status='passed',freeze_sha256=freeze_sha,validation_bindings=bindings,files=list(hashes.files.values())))
        write_json(root/'report.json',clean(dict(status='completed',freeze_sha256=freeze_sha,population_sha256=population_hash,
                   receipts=bindings,metrics=financial_metrics(results),objective=scored,validation_tuning=False,
                   final_input_audit_sha256=file_hash(root/'final_input_audit.json'))))
        emit(status='completed',stage='Frozen evaluation and financial audit complete',validation_status='Evaluated once')
        return 0
    except BaseException as error:
        emit(status='interrupted' if isinstance(error,InterruptedError) else 'failed',
             stage='Frozen evaluation stopped',error=str(error))
        raise
    finally:evaluator.close();lock.unlink(missing_ok=True)

if __name__=='__main__':raise SystemExit(main())
