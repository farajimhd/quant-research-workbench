"""Read-only manual intervention history; validation is display-only."""
import json
import time
from functools import lru_cache
from pathlib import Path
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from .progress_sessions import read_verified


def changes(before, after, prefix=''):
    if isinstance(before,dict) and isinstance(after,dict):
        return [r for k in sorted(set(before)|set(after)) for r in changes(before.get(k),after.get(k),prefix+'.'+k if prefix else k)]
    if isinstance(before,(list,tuple)) and isinstance(after,(list,tuple)):
        return [r for i in range(max(len(before),len(after))) for r in changes(before[i] if i<len(before) else None,after[i] if i<len(after) else None,f'{prefix}[{i}]')]
    return [] if before==after else [f'{prefix}: {before!r} -> {after!r}']


def index(root):
    root=Path(root);review=root/'agent-reviews';rows={}
    for path in sorted(review.glob('epoch-*/package.json')):
        p=read_verified(path);epoch=p['review_epoch']
        rows[epoch]=dict(epoch=epoch,source=p['source_generation'],count=len(p['offspring']),
            status='Prepared',generation=None,rationale=p['rationale'])
    for path in sorted((review/'inbox').glob('*.json')):
        p=read_verified(path);epoch=p['review_epoch']
        rows[epoch]=dict(epoch=epoch,source=p['source_generation'],count=len(p['offspring']),
            status='Pending',generation=None,rationale=p['rationale'])
    for path in sorted((review/'decisions').glob('generation-*.json')):
        decision=read_verified(path);p=decision.get('package')
        if p is None:continue
        generation=int(path.stem.split('-')[-1]);folder=root/path.stem
        seal_path=folder/'complete.json'
        if not seal_path.exists():continue
        seal=read_verified(seal_path)
        receipt=read_verified(folder/'agent-intervention.json',seal['agent_intervention_sha256'])
        from .distributed_assets import sha
        if receipt['decision_sha256']!=sha(path):raise ValueError('Intervention decision changed')
        rows[p['review_epoch']]=dict(epoch=p['review_epoch'],source=p['source_generation'],
            count=len(p['offspring']),status='Sealed' if p['offspring'] else 'Deferred / no offspring',
            generation=generation,rationale=p['rationale'])
    return sorted(rows.values(),key=lambda r:r['epoch'],reverse=True)


@lru_cache(maxsize=512)
def measured(root,generation,serials):
    from .progress_performance import load
    values=load(root,generation)
    return {s:values[s] for s in serials if s in values}


def cohort(root,epoch):
    root=Path(root);item=next(r for r in index(root) if r['epoch']==epoch)
    if item['generation'] is None:return dict(**item,children=[])
    generation=item['generation'];folder=root/f'generation-{generation:04d}'
    seal=read_verified(folder/'complete.json')
    receipt=read_verified(folder/'agent-intervention.json',seal['agent_intervention_sha256'])
    decision=read_verified(root/f'agent-reviews/decisions/generation-{generation:04d}.json',receipt['decision_sha256'])
    package=decision['package']
    from .agent_intervention import evidence
    source,_=evidence(root,package['source_generation'])
    population=read_verified(source/'population.json')
    parents=dict(zip(population['ids'],population['population']))
    if len(receipt['new_ids'])!=len(package['offspring']):raise ValueError('Offspring identity count mismatch')
    serials=tuple(receipt['new_ids']);parent_ids=tuple(sorted({p['parent'] for p in package['offspring']}))
    original=measured(str(root),package['source_generation'],parent_ids)
    history=[]
    for path in sorted(root.glob('generation-*/complete.json')):
        gen=int(path.parent.name.split('-')[-1])
        if gen>=generation and serials:history.append((gen,measured(str(root),gen,serials)))
    children=[]
    for serial,p in zip(serials,package['offspring']):
        children.append(dict(serial=serial,parent=p['parent'],kind='Control' if p['hypothesis'].startswith('control:') else 'Repair',
            hypothesis=p['hypothesis'],changes=changes(parents[p['parent']],p['payload']),
            parent_metrics=original.get(p['parent'],{}),
            history=[dict(generation=gen,metrics=values.get(serial)) for gen,values in history]))
    return dict(**item,children=children)


def get(model):
    root=model.global_root()/'global';epoch=getattr(model,'intervention_epoch',None)
    args=(str(root),) if epoch is None else (str(root),epoch)
    name='index' if epoch is None else 'cohort';key=('interventions',name,args,int(time.time()//15))
    try:
        if model.io:value=model.io.request(key,__name__,name,*args)
        else:value=globals()[name](*args)
        if value is not None:model.intervention_cache=(epoch,value)
        model.intervention_error=None
    except (OSError,ValueError,KeyError,IndexError) as e:
        model.intervention_error=str(e)
    cache=getattr(model,'intervention_cache',(None,None))
    return cache[1] if cache[0]==epoch else None


def key(model,value,height):
    if value=='back':
        if getattr(model,'intervention_child',None) is not None:model.intervention_child=None
        elif getattr(model,'intervention_epoch',None) is not None:model.intervention_epoch=None
        else:model.view='candidates'
        model.selection=0;return
    data=get(model)
    if data is None:return
    child=getattr(model,'intervention_child',None)
    rows=data if isinstance(data,list) else data['children']
    if child is not None:rows=detail_lines(next(r for r in rows if r['serial']==child))
    step=max(1,height-12)
    if value in ('j','k','n','b','home','end'):
        model.selection=max(0,min(len(rows)-1,0 if value=='home' else len(rows)-1 if value=='end' else model.selection+{'j':1,'k':-1,'n':step,'b':-step}[value]));return
    if value in ('\r','\n') and rows and child is None:
        row=rows[min(model.selection,len(rows)-1)]
        if isinstance(data,list):model.intervention_epoch=row['epoch']
        else:model.intervention_child=row['serial']
        model.selection=0


def fmt(x):return '-' if x is None else f'{x:,.2f}'


def detail_lines(child):
    lines=['Hypothesis: '+child['hypothesis'],'Exact genome changes:']+child['changes']
    for role in ('training','validation'):
        parent=child['parent_metrics'].get(role,{})
        lines.append(f"Parent {role}: net ${fmt(parent.get('net_pnl'))}; trades {fmt(parent.get('closed_positions'))}; win {fmt(parent.get('win_rate'))}%; profitable sessions {parent.get('profitable_sessions','-')}/{parent.get('coverage','-')}")
    for row in child['history']:
        if row['metrics'] is None:lines.append(f"Generation {row['generation']}: ID absent from evaluated cohort (descendants not included)");continue
        for role in ('training','validation'):
            m=row['metrics'].get(role,{});p=child['parent_metrics'].get(role,{})
            delta=m['net_pnl']-p['net_pnl'] if 'net_pnl' in m and 'net_pnl' in p else None
            lines.append(f"Gen {row['generation']} {role}: net ${fmt(m.get('net_pnl'))} | delta parent ${fmt(delta)} | score {fmt(m.get('score'))} | trades {fmt(m.get('closed_positions'))} | win {fmt(m.get('win_rate'))}% | profitable {m.get('profitable_sessions','-')}/{m.get('coverage','-')} | DD sum ${fmt(m.get('drawdown'))}")
    return lines


def render(model,width,height):
    data=get(model);layout=Layout();layout.split_column(Layout(name='head',size=4),Layout(name='body'),Layout(name='keys',size=3))
    epoch=getattr(model,'intervention_epoch',None);child=getattr(model,'intervention_child',None)
    layout['head'].update(Text('V66 MANUAL INTERVENTIONS'+(f' | review {epoch}' if epoch is not None else '')+(f' | candidate {child}' if child is not None else '')+'\nTraining guides edits; validation shown only for reporting. Parent comparisons use frozen source generation.\n'+(getattr(model,'intervention_error',None) or 'Sealed replay results; absent IDs are not zero profit. Refresh every 15 seconds.'),style='cyan'))
    table=Table(expand=True);limit=max(1,height-12);start=model.selection//limit*limit
    if data is None:table.add_column('Status');table.add_row('Loading intervention history…')
    elif child is not None:
        row=next(r for r in data['children'] if r['serial']==child);lines=detail_lines(row)
        table.add_column('Exact edits and generation results',overflow='fold')
        for line in lines[model.selection:model.selection+max(1,limit//3)]:table.add_row(Text(line))
    elif isinstance(data,list):
        for label in ('Review','Source gen','Admitted gen','Offspring','Status'):table.add_column(label)
        for i,r in enumerate(data[start:start+limit],start):table.add_row(str(r['epoch']),str(r['source']),str(r['generation'] or '-'),str(r['count']),r['status'],style='bold yellow on blue' if i==model.selection else None)
        if not data:table.add_row('-','-','-','0','No manual reviews yet')
    elif not data['children']:
        table.add_column('Review outcome');table.add_row(Text(data['rationale']))
    else:
        for label in ('Child','Parent','Type','First gen','Train net $','Delta parent $','Val net $','Edits'):table.add_column(label)
        for i,r in enumerate(data['children'][start:start+limit],start):
            first=r['history'][0];m=first['metrics'] or {};train=m.get('training',{});parent=r['parent_metrics'].get('training',{})
            delta=train['net_pnl']-parent['net_pnl'] if 'net_pnl' in train and 'net_pnl' in parent else None
            table.add_row(str(r['serial']),str(r['parent']),r['kind'],str(first['generation']),fmt(train.get('net_pnl')),fmt(delta),fmt(m.get('validation',{}).get('net_pnl')),str(len(r['changes'])),style='bold yellow on blue' if i==model.selection else None)
    layout['body'].update(Panel(table,title='Review epochs' if epoch is None else 'Changes and outcomes',padding=0))
    layout['keys'].update(Text('Arrows: select | Enter: open epoch / offspring | N/B: page | Esc: back\nZ: manual interventions | Q: close panel | Detailed history excludes descendants'))
    return layout
