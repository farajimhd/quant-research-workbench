"""One-time, training-only V7 seed selection; no replay or validation access."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
from .genome import Individual,sample
from .lineage import program_key
from .prepare_validation import require_finished
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v7.run_search import digest

VERSION='v8-mixed-initial-population-v1'


def diverse_seeds(population,ordered,rank,limit,similarity=.98):
    if not 0 < similarity <= 1:raise ValueError('Similarity must be in (0,1]')
    # Uncentered cosine preserves the importance of sparse activity and loss signs.
    def normalized(key):
        values=np.asarray([v[key] for v in ordered],dtype=np.float64).T
        if not np.isfinite(values).all():raise ValueError('Nonfinite training profile')
        norm=np.linalg.norm(values,axis=1,keepdims=True)
        return values/np.maximum(norm,1e-300)
    pnl=normalized('net_pnl');activity=normalized('closed_positions')
    selected=[];seen=set();rejected=0
    for i in rank:
        if len(selected)>=limit:break
        key=program_key(population[i])
        if key in seen or (selected and np.any((pnl[selected]@pnl[i]>=similarity)&
                                                (activity[selected]@activity[i]>=similarity))):
            rejected+=1;continue
        if not np.any(activity[i]):continue
        selected.append(i);seen.add(key)
    return selected,rejected


def mix(population,ordered,rank,size,seed=2237,fraction=.1,similarity=.98):
    if not 10<=size<=65536 or not 0<=fraction<=1:raise ValueError('Invalid initial population bounds')
    selected,rejected=diverse_seeds(population,ordered,rank,int(size*fraction),similarity)
    members=[Individual.restore(population[i].payload()) for i in selected]
    keys={program_key(m) for m in members};rng=np.random.default_rng(seed);attempts=0
    while len(members)<size:
        attempts+=1
        if attempts>size*32:raise ValueError('Unique initial sampling budget exhausted')
        member=sample(rng,1)[0];key=program_key(member)
        if key not in keys:members.append(member);keys.add(key)
    return members,dict(selected_candidates=selected,seed_count=len(selected),random_count=size-len(selected),
        requested_seed_count=int(size*fraction),similarity_rejections=rejected,similarity=similarity,
        fraction=fraction,random_seed=seed,validation_feedback=False)


def reconstruct(campaign):
    """Reproduce each evaluated population, checking every persisted identity."""
    from research.vectorized_backtest.v7.genome import sample as original_sample
    from research.vectorized_backtest.v7.diversity import summarize,next_population
    saved=json.loads((campaign/'checkpoint.json').read_text(encoding='utf-8-sig'))
    contract=saved['contract'];rng=np.random.default_rng(contract['seed'])
    population=original_sample(rng,contract['population']);generation=saved['completed_generations'];start=1
    # A migrated campaign can contain earlier generations from another selector.
    # Use its hash-bound predecessor checkpoint as the exact RNG/population anchor.
    handoff=campaign/'handoff.json'
    if handoff.exists():
        migration=json.loads(handoff.read_text(encoding='utf-8-sig'))
        predecessor=Path(migration['old_campaign'])/'checkpoint.json'
        if file_hash(predecessor)!=migration['old_checkpoint_sha256']:raise ValueError('V7 predecessor checkpoint changed')
        anchor=json.loads(predecessor.read_text(encoding='utf-8-sig'))
        start=anchor['completed_generations']+1
        if start!=migration['completed_generations']+1 or not 1<=start<=generation:
            raise ValueError('Invalid V7 reconstruction anchor')
        if digest(anchor['population'])!=migration['population_sha256']:raise ValueError('V7 anchor population mismatch')
        population=[Individual.restore(v) for v in anchor['population']]
        rng.bit_generator.state=anchor['rng_state']
    days=sorted(contract['input_receipts'])
    for number in range(start,generation+1):
        root=campaign/f'generation-{number:04d}'
        seal=json.loads((root/'complete.json').read_text(encoding='utf-8-sig'))
        if seal.get('validation_opened') is not False or seal['training_days']!=days:
            raise ValueError('V7 seed source must be a complete training panel')
        token=digest([m.payload() for m in population])
        if token!=seal['population_sha256']:raise ValueError(f'V7 reconstruction mismatch at generation {number}')
        ordered=[]
        for day in days:
            path=root/day/'receipt.json'
            if file_hash(path)!=seal['session_receipts'][day]:raise ValueError('V7 training receipt changed')
            receipt=json.loads(path.read_text(encoding='utf-8-sig'))
            if receipt['population_sha256']!=token or receipt.get('validation_opened') is not False:
                raise ValueError('V7 receipt identity/split mismatch')
            ordered.append(receipt['metrics'])
        rows=json.loads((root/'ranking.json').read_text(encoding='utf-8-sig'))
        rank=[r['candidate'] for r in rows]
        if len(set(rank))!=len(rank) or any(not 0<=i<len(population) for i in rank):raise ValueError('Invalid V7 ranks')
        # Ranking is not included in the old seal: independently recompute it.
        import torch
        from research.vectorized_backtest.v6.torch_backtest.stability import LowerTailDollarObjective,score
        from research.vectorized_backtest.v7.genome import effective_rules
        from research.vectorized_backtest.v7.features import CATALOG
        matrix=lambda k:torch.tensor([v[k] for v in ordered],dtype=torch.bool if k=='terminal_valid' else torch.float64)
        complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in effective_rules(m).values())+len(m.minimum_age) for m in population],dtype=torch.float64)
        scores=score(*(matrix(k) for k in ('net_pnl','drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','terminal_valid')),
            complexity,config=LowerTailDollarObjective(**contract['objective']),inactivity=matrix('inactivity_fraction').mean(0))
        expected=sorted(np.flatnonzero(scores['feasible'].numpy()).tolist(),key=lambda i:(-float(scores['score'][i]),i))
        if rank!=expected:raise ValueError('V7 ranking differs from sealed training metrics')
        if number==generation:return population,ordered,rank,dict(generation=generation,population_sha256=token,
            seal_sha256=file_hash(root/'complete.json'),checkpoint_sha256=file_hash(campaign/'checkpoint.json'),
            campaign=str(campaign),ranking_sha256=file_hash(root/'ranking.json'))
        representatives,_,_=summarize(population,ordered,rank)
        population,_=next_population(rng,population,representatives)
    raise ValueError('No completed V7 generation')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v7-campaign',type=Path,required=True)
    parser.add_argument('--stopped-v7-receipt',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--population',type=int,default=1024)
    parser.add_argument('--seed',type=int,default=2237)
    parser.add_argument('--v7-fraction',type=float,default=.1)
    parser.add_argument('--similarity',type=float,default=.98)
    args=parser.parse_args();require_finished(args.v7_campaign,args.stopped_v7_receipt)
    output=require_runtime(args.output.parent)/args.output.name
    if output.exists():raise ValueError('Initial population output is immutable; choose a new path')
    population,ordered,rank,source=reconstruct(args.v7_campaign)
    members,report=mix(population,ordered,rank,args.population,args.seed,args.v7_fraction,args.similarity)
    payload=[m.payload() for m in members]
    write_json(output,dict(version=VERSION,population=payload,population_sha256=digest(payload),source=source,initialization=report))
    print(json.dumps(dict(source=source,initialization=report)),flush=True)


if __name__=='__main__':main()
