"""Attach causal market states to every frozen supervised decision.

Preserves the original research labels without treating their hypothetical
prior-low stops or fixed target outcomes as actual executed positions.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path

from strategy_222_supervised_research import digest,save
from strategy_222_feature_comparison import features,epoch
from strategy_222_market_state_features import MarketStates
from strategy_222_macd_episodes import closed_connection


def enrich(row,sequence,stamp,decision,market):
    if row['id']!=f"{row['run_id']}:{sequence}" or epoch(stamp)!=row['at']:
        raise ValueError('Decision identity/time mismatch')
    if decision.get('ticker')!=row['symbol'] or not any(
            str(s).startswith(f"qmd-derived:{row['symbol']}:1s:") for s in decision.get('source_signal_ids',[])):
        raise ValueError('Wrong symbol or source timeframe')
    extra,authority=market.at(row['symbol'],decision,row['at'])
    return dict(id=row['id'],run_id=row['run_id'],symbol=row['symbol'],window=row['window'],at=row['at'],
        sequence=sequence,first_blocker=decision['reason'],
        features=dict(features(decision.get('metadata') or {},row['at']),**extra),
        market_state_authority=authority)


def run(source,cache_map,output):
    output=output.resolve();output.relative_to(Path('D:/TradingML/runtimes').resolve())
    output.mkdir(parents=True,exist_ok=True)
    source_manifest=source/'dataset-manifest.json';d=json.loads(source_manifest.read_text())
    mapping=json.loads(cache_map.read_text())
    if d['status']!='screenable' or mapping['missing'] or mapping['dataset_manifest_sha256']!=digest(source_manifest):
        raise ValueError('Incomplete or changed source authority')
    files=[source_manifest,source/'features.json',source/'labels.json',cache_map,Path(__file__)]
    files.extend(Path(__file__).with_name(name) for name in (
        'strategy_222_feature_comparison.py','strategy_222_market_state_features.py',
        'strategy_222_macd_episodes.py','strategy_222_supervised_research.py'))
    identity={str(p.resolve()):digest(p) for p in files}
    rows=json.loads((source/'features.json').read_text());labels=json.loads((source/'labels.json').read_text())
    ids=[r['id'] for r in rows]
    if len(set(ids))!=len(ids) or ids!=[l['id'] for l in labels]:raise ValueError('Label identity/order mismatch')
    by_run=defaultdict(list)
    for row in rows:by_run[row['run_id']].append(row)
    if set(by_run)!=set(mapping['runs']):raise ValueError('Run coverage mismatch')
    manifest=output/'manifest.json'
    state=json.loads(manifest.read_text()) if manifest.exists() else dict(identity=identity,runs={})
    if state['identity']!=identity:raise ValueError('Inputs changed; use a successor directory')
    if state.get('status')=='completed':
        for name,sha in state['outputs'].items():
            if digest(output/name)!=sha:raise ValueError('Completed aggregate changed')
    state.update(status='running',active=1,failed=0,total_runs=len(by_run))
    save(manifest,state)
    collected=[]
    try:
        for rid,requested in sorted(by_run.items()):
            authority=mapping['runs'][rid];cache=Path(authority['cache']);summary=Path(authority['summary'])
            if digest(cache)!=authority['cache_sha256'] or digest(summary)!=authority['summary_sha256']:
                raise ValueError('Cache or replay summary changed')
            journal=summary.parent/'journal.sqlite3'
            pinned={str(Path(k).resolve()):v for k,v in d['inputs'].items()}
            if str(journal.resolve()) not in pinned or digest(journal)!=pinned[str(journal.resolve())]:
                raise ValueError('Original decision journal changed')
            target=output/f'{rid}.json'
            prior=state['runs'].get(rid)
            if prior:
                if digest(target)!=prior['sha256']:raise ValueError('Completed run output changed')
                enriched=json.loads(target.read_text())
            else:
                market=MarketStates(cache,json.loads(summary.read_text()))
                by_sequence={int(row['id'].rsplit(':',1)[1]):row for row in requested}
                connection=closed_connection(journal);found={}
                try:
                    sequences=sorted(by_sequence)
                    for start in range(0,len(sequences),500):
                        chunk=sequences[start:start+500]
                        query='select sequence,event_time,payload_json from journal where run_id=? and category=\'strategy_decision\' and sequence in ('+','.join('?' for _ in chunk)+')'
                        for sequence,stamp,raw in connection.execute(query,[rid,*chunk]):
                            found[sequence]=enrich(by_sequence[sequence],sequence,stamp,json.loads(raw),market)
                finally:connection.close()
                if set(found)!=set(by_sequence):raise ValueError('Missing source decisions')
                enriched=[found[int(row['id'].rsplit(':',1)[1])] for row in requested]
                save(target,enriched)
                state['runs'][rid]=dict(sha256=digest(target),count=len(enriched))
                save(manifest,state)
            collected.extend(enriched)
            print(f"Completed={len(state['runs'])}/{len(by_run)} active=0 queued={len(by_run)-len(state['runs'])} failed=0; {authority['symbol']}: {len(enriched)} decisions",flush=True)
        by_id={row['id']:row for row in collected};ordered=[by_id[key] for key in ids]
        save(output/'features.json',ordered)
        # Preserve label bytes exactly, including the original label policy.
        (output/'labels.json').write_bytes((source/'labels.json').read_bytes())
        state.update(status='completed',active=0,rows=len(ordered),
            feature_count=len({k for row in ordered for k in row['features']}),
            feature_delivery=dict(Counter(f"{tf}:{item['status']}" for row in ordered for tf,item in row['market_state_authority'].items())),
            label_policy=d['label_policy'],limitations=d['limitations'],
            outputs={name:digest(output/name) for name in ('features.json','labels.json')})
        save(manifest,state)
        print(f"Complete: {state['rows']} supervised decisions, {state['feature_count']} features. {state['feature_delivery']}",flush=True)
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',active=0,failed=int(not isinstance(exc,KeyboardInterrupt)),error=str(exc))
        save(manifest,state);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--cache-map',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.source,args.cache_map,args.output)
