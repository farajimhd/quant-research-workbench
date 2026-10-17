"""Deferred post-Phase-2 analysis. Idle gate imports no Torch or engine code."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','POLARS_MAX_THREADS'):os.environ[key]='1'
import argparse,sys,time,subprocess
from pathlib import Path
from .contracts import read,write,file_hash,digest,completed,ensure_idle,memory_admission,analysis_owner

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v26')


def analysis_source():
    return digest({p.name:p.read_text(encoding='utf-8').replace('\r\n','\n') for p in sorted(Path(__file__).parent.glob('*.py'))})


def finished(args,target,proof):
    write(args.output/'latest-complete.json',dict(report=str(target/'report.html'),checkpoint_sha256=proof['checkpoint_sha256'],analysis_source_sha256=analysis_source()))
    if args.scheduled:
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',"Disable-ScheduledTask -TaskName 'Codex-V26-Final-Occupancy' | Out-Null"],check=True)
    print('COMPLETE:',target/'report.html')


def analyze(proof,pointer,output,environment,driver_source,driver_cache):
    import numpy as np
    from .occupancy import intervals,intersection_matrix,metrics
    from .report import summarize,render
    output.mkdir(parents=True,exist_ok=True)
    contract=proof['saved']['contract'];train=Path(proof['roles']['training']['folder']);valid=Path(proof['roles']['validation']['folder'])
    population=read(train/'population.json');state=proof['saved']['state']
    if population['population']!=state['population'] or population['ids']!=state['ids']:raise ValueError('Final population differs from sealed checkpoint')
    training_rows=state['leaders'];validation_rows=sorted(state['validation_ranking'],key=lambda r:r['candidate'])
    byserial={int(x['serial']):x for x in training_rows};indices={int(s):i for i,s in enumerate(population['ids'])}
    if not 1<=len(validation_rows)<=512 or len({r['serial'] for r in validation_rows})!=len(validation_rows):raise ValueError('Invalid frozen validation shortlist')
    if [r['candidate'] for r in validation_rows]!=list(range(1,len(validation_rows)+1)):raise ValueError('Validation order changed')
    candidates=[dict(serial=r['serial'],training_rank=byserial[r['serial']]['rank'],training_profit=byserial[r['serial']]['metrics']['total_pnl'],validation_profit=r['metrics']['total_pnl']) for r in validation_rows]
    payload=[population['population'][indices[c['serial']]] for c in candidates]
    token=digest(payload)
    if digest(population['population'])!=proof['roles']['training']['seal']['population_sha256'] or token!=proof['roles']['validation']['seal']['population_sha256']:
        raise ValueError('Final candidate payload/shortlist identity changed')
    result=dict(version='v26-posthoc-occupancy-v1',posthoc_only=True,selection='frozen training-selected final validation shortlist',
        owner=proof['owner'],checkpoint_sha256=proof['checkpoint_sha256'],seals=proof['seals'],candidates=candidates,splits={},analysis_source_sha256=analysis_source())
    write(output/'frozen-candidates.json',dict(candidates=candidates,payload=payload,population_sha256=token))
    days=contract['validation']['days'];workers=contract['session_workers'];widths={};needs_trace=[]
    for first in range(0,len(days),workers):
        cohort=days[first:first+workers];width=0
        for day in cohort:
            receipt=read(valid/day/'receipt.json');width=max(width,len(receipt['listing_ids']))
            if not all(k in receipt['metrics'] for k in ('episode_entry_clocks','episode_exit_clocks')):needs_trace.append(day)
        widths.update({day:width for day in cohort})
    pending_trace=[]
    for day in needs_trace:
        verified=output/'validation-trace/generation-0050'/day/'exact-parity.json'
        if verified.exists():
            item=read(verified)
            if item['receipt_sha256']!=file_hash(verified.parent/'receipt.json') or item['saved_sha256']!=proof['roles']['validation']['seal']['session_receipts'][day]:raise ValueError('Saved trace evidence changed')
        else:pending_trace.append(day)
    job=dict(owner=proof['owner'],contract=contract,payload=payload,token=token,widths=widths,pointer=str(pointer),output=str(output/'validation-trace'),saved_folder=str(valid),
        receipt_hashes=proof['roles']['validation']['seal']['session_receipts'],environment=str(environment),driver_source=str(driver_source),driver_cache=str(driver_cache),trace_days=pending_trace)
    write(output/'trace-job.json',job)
    for role in ('training','validation'):
        ensure_idle(pointer);memory_admission()
        matrix=output/(role+'.npz');sealfile=output/(role+'-complete.json')
        if sealfile.exists():
            split=read(sealfile)
            if split['checkpoint_sha256']!=proof['checkpoint_sha256'] or file_hash(matrix)!=split['matrix_sha256']:raise ValueError('Posthoc resume identity changed')
            result['splits'][role]=split;continue
        if role=='validation' and pending_trace:
            # One immutable-engine worker, original batch size/order/cohort widths.
            # All old financial fields must match exactly before clocks are used.
            trace=Path(__file__).with_name('validation_trace.py')
            process=subprocess.run([sys.executable,'-B',str(trace),str(output/'trace-job.json')])
            if process.returncode==75:raise InterruptedError('Trace worker yielded to optimizer or RAM pressure')
            process.check_returncode()
        n=len(candidates);shared=np.zeros((n,n),dtype=np.int64);durations=np.zeros(n,dtype=np.int64);receipts={};sessions=[]
        for day in contract[role]['days']:
            ensure_idle(pointer);memory_admission();started=time.perf_counter()
            saved=Path(proof['roles'][role]['folder'])/day/'receipt.json'
            expected=proof['roles'][role]['seal']['session_receipts'][day]
            if file_hash(saved)!=expected:raise ValueError('Sealed receipt changed during analysis')
            f=output/'validation-trace/generation-0050'/day/'receipt.json' if role=='validation' and day in needs_trace else saved
            cache=output/'session-matrices'/role/(day+'.npz');cache_seal=cache.with_suffix('.json')
            if cache_seal.exists():
                cache_info=read(cache_seal)
                if cache_info['receipt_sha256']!=file_hash(f) or cache_info['checkpoint_sha256']!=proof['checkpoint_sha256'] or cache_info['matrix_sha256']!=file_hash(cache):raise ValueError('Session matrix identity changed')
                with np.load(cache,allow_pickle=False) as stored:shared+=stored['shared'];durations+=stored['durations']
                receipts[day]=dict(saved_sha256=expected,trace_sha256=file_hash(f));sessions.append(cache_info['session']);continue
            receipt=read(f);m=receipt['metrics'];selected=[indices[c['serial']] for c in candidates] if role=='training' else list(range(n))
            patterns=[intervals(m['episode_listings'][i],m['episode_entry_clocks'][i],m['episode_exit_clocks'][i],listing_count=len(receipt['listing_ids'])) for i in selected]
            amount,held,details=intersection_matrix(patterns);shared+=amount;durations+=held
            session=dict(day=day,seconds=time.perf_counter()-started,**details)
            cache.parent.mkdir(parents=True,exist_ok=True);temporary=cache.with_name(day+'.tmp.npz');np.savez_compressed(temporary,shared=amount,durations=held);temporary.replace(cache)
            write(cache_seal,dict(checkpoint_sha256=proof['checkpoint_sha256'],matrix_sha256=file_hash(cache),receipt_sha256=file_hash(f),session=session))
            receipts[day]=dict(saved_sha256=expected,trace_sha256=file_hash(f));sessions.append(session)
            write(output/'status.json',dict(status='running',split=role,completed=len(sessions),total=len(contract[role]['days']),day=day))
            del patterns,receipt,m
        calculated=metrics(shared,durations);temporary=matrix.with_name(role+'.tmp.npz')
        np.savez_compressed(temporary,intersection_seconds=shared,occupied_seconds=durations,serials=np.array([c['serial'] for c in candidates]),**calculated);temporary.replace(matrix)
        split=dict(checkpoint_sha256=proof['checkpoint_sha256'],matrix_sha256=file_hash(matrix),receipts=receipts,sessions=sessions,summary=summarize(calculated,candidates))
        write(sealfile,split);result['splits'][role]=split
    ensure_idle(pointer)
    final=completed(pointer)
    if final is None or final['checkpoint_sha256']!=proof['checkpoint_sha256'] or final['owner']['campaign']!=proof['owner']['campaign']:raise ValueError('Final campaign changed during analysis')
    write(output/'result.json',result);render(result,output/'report.html')
    write(output/'complete.json',dict(checkpoint_sha256=proof['checkpoint_sha256'],result_sha256=file_hash(output/'result.json'),report_sha256=file_hash(output/'report.html')))
    write(output/'status.json',dict(status='complete',candidates=len(candidates),splits=['training','validation']))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pointer',type=Path,default=RUNTIME/'active-campaign.json')
    parser.add_argument('--output',type=Path,default=RUNTIME/'diagnostics/post-phase2-occupancy')
    parser.add_argument('--environment',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v20/diagnostics/timing-audit-20261015/profile-environment.json'))
    parser.add_argument('--driver-source',type=Path,default=RUNTIME/'diagnostics/20261017-rule-optimization/driver-source.json')
    parser.add_argument('--driver-cache',type=Path,default=RUNTIME/'diagnostics/20261017-rule-optimization/driver-cache')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--scheduled',action='store_true',help='Disable the dedicated occupancy task after successful completion')
    args=parser.parse_args(argv)
    if not args.output.resolve().is_relative_to((RUNTIME/'diagnostics').resolve()):raise ValueError('Use the V26 runtime diagnostics root')
    proof=completed(args.pointer)
    if proof is None:
        write(args.output/'deferred-status.json',dict(status='waiting',reason='Waiting for successful global generation 50, seals and ownership release'))
        print('WAITING: optimizer unchanged; no engine/GPU imports');return 0
    target=args.output/(proof['checkpoint_sha256'][:16]+'-'+analysis_source()[:8])
    if args.check_only:print('READY: final campaign sealed and released');return 0
    if (target/'complete.json').exists():
        done=read(target/'complete.json')
        if done['checkpoint_sha256']!=proof['checkpoint_sha256'] or file_hash(target/'result.json')!=done['result_sha256'] or file_hash(target/'report.html')!=done['report_sha256']:raise ValueError('Completed posthoc evidence changed')
        finished(args,target,proof);return 0
    try:
        with analysis_owner(args.output):analyze(proof,args.pointer,target,args.environment,args.driver_source,args.driver_cache)
    except InterruptedError as e:
        write(args.output/'deferred-status.json',dict(status='waiting',reason=str(e)));return 0
    except Exception:
        import traceback
        write(args.output/f'failure-{time.time_ns()}.json',dict(status='failed',error=traceback.format_exc()))
        raise
    finished(args,target,proof);return 0


if __name__=='__main__':raise SystemExit(main())
