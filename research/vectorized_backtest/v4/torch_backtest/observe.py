"""Read-only terminal dashboard: one renderer, no experiment writes."""
import argparse,json,time,os
from pathlib import Path
from rich.console import Console
from rich.live import Live
from .dashboard import render

def completed_metrics(output, generation):
    """Recover display-only metrics from a completed training generation."""
    from .run_search import metric_summary,restore,fingerprint
    from .runtime import file_hash
    record=json.loads((output/f'generation_{generation-1:03d}'/'generation.json').read_text())
    scores=record['scores'];population=record['population']
    if fingerprint(population)!=record['population_sha256']:
        raise ValueError('Completed population hash mismatch')
    results=[]
    for binding in record['receipts']:
        recorded=Path(binding['path'])
        if recorded.parent.parent.name!=f'generation_{generation-1:03d}':raise ValueError('Receipt generation mismatch')
        path=output/recorded.parent.parent.name/recorded.parent.name/recorded.name
        if file_hash(path)!=binding['sha256']:raise ValueError('Completed receipt hash mismatch')
        receipt=json.loads(path.read_text())
        if receipt['population_sha256']!=record['population_sha256']:raise ValueError('Receipt population mismatch')
        results.append(receipt['metrics'])
    if len(results)!=30:raise ValueError('Completed generation requires 30 sessions')
    top=max(range(len(population)),key=lambda i:(scores['feasible'][i],-scores['violation'][i],scores['score'][i]))
    return dict(closest_score=scores['score'][top],closest_violation=scores['violation'][top],
                closest_metrics=metric_summary(results,scores,top,[restore(v) for v in population]),
                feasible_candidates=sum(scores['feasible']))

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--view',choices=('financial','positions','performance','objective','messages'),default='financial');args=p.parse_args(argv)
    console=Console(no_color=bool(os.environ.get('NO_COLOR')));last={};error=None;financial_page=0;objective_page=0;recovered={}
    identity_path=args.output/'identity.json'
    # Older immutable workers do not emit mode. Derive display scope from their
    # retained launch identity without editing their status or experiment.
    profile=False;profile_sessions=1;objective=None
    if identity_path.exists():
        identity=json.loads(identity_path.read_text(encoding='utf-8'))
        profile=bool(identity.get('arguments',{}).get('profile',False));objective=identity.get('objective')
        profile_sessions=identity.get('profile_sessions',1)
    def read():
        nonlocal last,error
        try:last=json.loads((args.output/'status.json').read_text(encoding='utf-8'));error=None
        except (FileNotFoundError,PermissionError,json.JSONDecodeError) as e:error=f'Snapshot unavailable: {type(e).__name__}; retaining last good view'
        if error:last={**last,'waiting_reason':error}
        generation=last.get('completed_generations',0)
        if not profile and generation and not last.get('best_metrics') and not last.get('closest_metrics'):
            try:
                if generation not in recovered:recovered[generation]=completed_metrics(args.output,generation)
                last={**last,**recovered[generation]}
            except (OSError,ValueError,KeyError,IndexError) as e:
                last={**last,'waiting_reason':f'Completed metrics unavailable: {e}'}
        if profile:
            last={**last,'mode':'profile','focus':f'{profile_sessions} training-session profile; validation SEALED',
                  'config':{**last.get('config',{}),'training_sessions':profile_sessions}}
            if last.get('status')=='training':last['status']='profiling'
        return render({**last,'objective':last.get('objective') or objective,'_financial_page':financial_page,'_objective_page':objective_page},width=console.width,height=console.height,view=args.view)
    if args.once or not console.is_terminal:
        console.print(read());return 0
    with Live(read(),console=console,refresh_per_second=1,screen=False,transient=False) as live:
        try:
            while True:
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':break
                        if key=='f':financial_page=financial_page+1 if args.view=='financial' else 0
                        if key=='c':objective_page=objective_page+1 if args.view=='objective' else 0
                        args.view={'f':'financial','t':'positions','p':'performance','c':'objective','m':'messages'}.get(key,args.view)
                live.update(read());time.sleep(1)
                # Retain the final panel and its navigation until Q/Ctrl-C.
                # The renderer is read-only and keeps no GPU allocations.
        except KeyboardInterrupt:pass
    return 0

if __name__=='__main__':raise SystemExit(main())
