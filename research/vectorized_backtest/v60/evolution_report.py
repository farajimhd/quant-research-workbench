"""Compact observational reports; no population mutation or selection input."""
import json,time,statistics
from pathlib import Path
from research.vectorized_backtest.v60.publication import write_json

FIELDS=('score','net_pnl','gross_losses','drawdown','closed_positions')

def operators(record):
    if record.get('blind_mix'):
        return sorted({'mix:'+record['blind_mix']['method']}|set(record['blind_mix'].get('operators',[])))
    if record.get('whole_network'):return ['whole:'+str(record.get('whole_mode','unknown'))]
    tags={a.get('join',a.get('mode','unknown')) for a in record.get('crossover',{}).get('actions',[])}
    return sorted(tags) or [record.get('operation','unavailable')]

def summarize(ids,panel,records,retained=None,generation=0):
    by_id={r['serial']:r for r in records};current={int(s):i for i,s in enumerate(ids)};groups={};rows=[]
    for i,serial in enumerate(ids):
        r=by_id[int(serial)];tags=operators(r);families=r.get('founder_groups',[]);methods=r.get('founder_methods',[])
        row=dict(serial=int(serial),generation=generation,operators=tags,founder_groups=families,founder_methods=methods,
            original_founder=r.get('global_founder',False),founder_index=r.get('founder_index'),founder_mask=r.get('founder_mask','0x0'),
            ancestors=r.get('ancestors',[]),source_day=r.get('source_day'),source_serial=r.get('source_serial'),source_generation=r.get('source_generation'),mutation_fields=r.get('mutation_fields',[]),crossover=r.get('crossover'),
            origin_mask=r.get('origin_mask','0x0'),origins=r.get('origins',[]),immigration_categories=r.get('immigration_categories',[]),
            source_sessions=r.get('source_sessions',[]),parents=r.get('parents',[]),operation=r.get('operation'),nodes=r.get('node_count'),branch_count=r.get('branch_count',1),
            mix=r.get('blind_mix'),eligible=bool(panel['eligible'][i]),retained=None if retained is None else int(serial) in retained,
            **{k:float(panel[k][i]) for k in FIELDS})
        row.update({k:float(panel[k][i]) for k in ('ticker_component','session_component','uncapped_score','profitable_sessions','session_targets_met','best_session_net','net_excluding_best_session') if k in panel})
        comparisons=r.get('parent_objectives',[])
        if r.get('blind_mix'):
            parents=r['blind_mix']['parent_serials'];comparisons=[panel['score'][current[p]] for p in parents if p in current]
            if len(comparisons)!=len(parents):comparisons=[]
        row['beat_best_parent']=bool(row['score']>max(comparisons)) if comparisons else None
        rows.append(row)
        categories=[('operator',tag) for tag in tags]+[('family',tag) for tag in families]+[('founder-method',tag) for tag in methods]+[('origin-session',tag) for tag in row['source_sessions']]+[('immigration',tag) for tag in row['immigration_categories']]
        for category,tag in categories:groups.setdefault((category,tag),[]).append(row)
    summaries=[]
    for (category,tag),members in sorted(groups.items()):
        comparisons=[r['beat_best_parent'] for r in members if r['beat_best_parent'] is not None]
        summaries.append(dict(category=category,group=tag,evaluated=len(members),original_founders=sum(r['original_founder'] for r in members),
            retained=sum(r['retained'] for r in members) if retained is not None else None,
            best_score=max(r['score'] for r in members),median_score=statistics.median(r['score'] for r in members),
            median_net=statistics.median(r['net_pnl'] for r in members),median_loss=statistics.median(r['gross_losses'] for r in members),
            median_drawdown=statistics.median(r['drawdown'] for r in members),
            median_nodes=statistics.median(r['nodes'] for r in members if r['nodes'] is not None) if any(r['nodes'] is not None for r in members) else None,
            parent_comparisons=len(comparisons),beat_best_parent=sum(comparisons)))
    return dict(generation=generation,scope='training',groups=summaries,candidates=rows,overlap='Multi-operator and descendant-family groups overlap; counts must not be summed.')

def publish(root,folder,ids,panel,records,retained,telemetry,generation,session_panels=None):
    start=time.perf_counter();root=Path(root);folder=Path(folder)
    report=summarize(ids,panel,records,retained,generation)
    report['next_generation_reproduction']=dict(whole_network=telemetry.get('whole_network'),accepted_mask_modes=telemetry.get('accepted_mask_modes'),invalid_crossover_attempts=telemetry.get('invalid_crossover_attempts'),fallbacks=telemetry.get('fallbacks'))
    previous=root/'evolution-report.json';old={}
    if previous.exists():old=json.loads(previous.read_bytes())
    prior={(r['category'],r['group']):r for r in old.get('groups',[])}
    for group in report['groups']:
        p=prior.get((group['category'],group['group']))
        group['median_score_change']=group['median_score']-p['median_score'] if p and old.get('generation')<generation else None
    original=root/'founder-results.json'
    founders=json.loads(original.read_bytes()) if original.exists() else {}
    for row in report['candidates']:
        if row['original_founder']:founders[str(row['serial'])]=dict(row,last_evaluated_generation=generation)
    if founders:write_json(folder/'founder-results.json',founders)
    if session_panels:write_json(folder/'session-metrics.json',dict(ids=list(map(int,ids)),sessions=session_panels))
    report['report_seconds']=time.perf_counter()-start
    write_json(folder/'evolution-report.json',report)
    return report

def expose(root,folder):
    """Publish mutable observer indexes only after checkpoint and seal publication."""
    root=Path(root);folder=Path(folder)
    for name in ('evolution-report.json','founder-results.json'):
        if (folder/name).exists():write_json(root/name,json.loads((folder/name).read_bytes()))

def historical(folder):
    """Read sealed evidence only; never fabricate missing founder attribution."""
    folder=Path(folder)
    if not (folder/'complete.json').exists():raise ValueError('Unsealed generation')
    train=json.loads((folder/'training.json').read_bytes());records=json.loads((folder/'evaluated-pedigree.json').read_bytes())
    retained=None;path=folder/'offspring-audit.json'
    if path.exists():retained=json.loads(path.read_bytes()).get('retained_ids')
    report=summarize(train['ids'],train['panel'],records,None if retained is None else set(retained),int(folder.name.split('-')[-1]))
    rpath=folder/'reproduction.json';report['next_generation_reproduction']=json.loads(rpath.read_bytes()).get('telemetry',{}) if rpath.exists() else None
    report['historical']=True;return report
