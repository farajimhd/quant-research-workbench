"""Replay observational mutants in spare batch capacity; publish separate evidence."""
import json
from pathlib import Path
from .identity import digest
from .lineage import program_key
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_assets import sha


def evaluate(controller,members,generation,day,width,payload,main_token):
    worker=controller.worker;probe=controller.paired_probe
    mutants=probe['mutants'];extra=[m.payload() for m in mutants]
    extra_token=digest(extra);combined=payload+extra;combined_token=digest(combined)
    if worker.population_preparer:worker.population_preparer.append_members(main_token,combined,mutants,combined_token)
    output=worker.config['output'];resident=worker.resident_results
    cache_path=Path(output)/'result-cache'/f'{day}.json'
    if resident is None and cache_path.exists():
        from .prefetch_pipeline import read_results
        from .resume_authority import accept_cache
        record=read_results(cache_path)[1];expected=dict(record['identity'],source=worker.config['source_sha256'])
        accept_cache(worker.config,cache_path,record,expected)
        resident=(expected,record['results'])
    diagnostic_keys={program_key(m) for m in mutants}
    # Diagnostic proposals must actually replay, even if their identity happened
    # to occur in an older training cohort. Never retain them in the parent cache.
    if resident:worker.resident_results=(resident[0],{k:v for k,v in resident[1].items() if k not in diagnostic_keys})
    worker.config['output']=str(controller.root/'paired-replay')
    try:
        worker.run(day,None,combined,generation,combined_token,width)
        folder=Path(worker.config['output'])/f'generation-{generation:04d}'/day
        raw=json.loads((folder/'receipt.json').read_bytes());metrics=raw['metrics'];n=len(members)
        if raw['population_sha256']!=combined_token:raise ValueError('Paired replay population changed')
        cache=worker.resident_results
        if cache is None:raise ValueError('Paired replay requires bounded session residency')
        keys=worker.population_preparer.future.result()[1][:n] if worker.population_preparer else [program_key(m) for m in members]
        selected={key:cache[1][key] for key in keys}
        worker.resident_results=(cache[0],selected)
        write_json(Path(output)/'result-cache'/f'{day}.json',dict(identity=cache[0],results=selected,results_sha256=digest(selected)))
        receipts=[]
        for start,end,token,destination,owners in (
            (0,n,main_token,Path(output),[m.case_owner for m in members]),
            (n,n+len(mutants),extra_token,controller.root/'mutation-probe-replay',[m.case_owner for m in mutants])):
            part={k:v[start:end] for k,v in metrics.items()};reused=sum(part['result_reused'])
            receipt=dict(raw,metrics=part,population_sha256=token,case_owners=owners,reused_candidates=reused,
                evaluated_candidates=end-start-reused,paired_replay=dict(receipt=str(folder/'receipt.json'),sha256=sha(folder/'receipt.json'),offset=start,count=end-start))
            path=destination/f'generation-{generation:04d}'/day/'receipt.json';write_json(path,receipt)
            receipts.append((part,{day:dict(receipt=str(path),receipt_sha256=sha(path))}))
        timings=json.loads((folder/'timings.json').read_bytes())
        for row in timings:row.update(paired_diagnostic_candidates=len(mutants),training_population=len(members))
        write_json(Path(output)/f'generation-{generation:04d}'/day/'timings.json',timings)
        # No second replay duration: both ledgers refer to the same GPU work.
        write_json(controller.root/'mutation-probe-replay'/f'generation-{generation:04d}'/day/'timings.json',[])
        probe['metrics'],probe['receipts']=receipts[1]
        return [receipts[0][0]],receipts[0][1]
    except BaseException:
        worker.resident_results=resident
        raise
    finally:worker.config['output']=output
