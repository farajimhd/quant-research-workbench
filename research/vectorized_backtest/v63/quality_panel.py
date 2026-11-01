"""Population quality, evolution and paired generalization navigation."""
import time
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def setup(model):
    if not hasattr(model,'quality_page'):
        model.quality_page=1;model.quality_generation=None;model.quality_role='training'
        model.quality_cohort='all';model.quality_group=None;model.quality_category='origin'
        model.quality_sort='score';model.quality_reverse=False;model.quality_serial=None
        model.quality_stat='median'


def context(model):
    setup(model);history=model.quality.index.get('history',[])
    generation=model.quality_generation if model.quality_generation is not None else max((r['generation'] for r in history),default=0)
    view=model.quality.request(generation,model.quality_role,model.quality_group,model.quality_cohort)
    return history,generation,view


def fmt(value):return '-' if value is None else f'{value:,.2f}' if isinstance(value,(int,float)) else str(value)
def dist(value):return ' / '.join(fmt(value.get(k)) for k in ('median','p90','best')) if isinstance(value,dict) else fmt(value)


METRICS=[('Candidates','count'),('Inactive count','inactive'),('Inactive %','inactive_pct'),
 ('No entry signal','no_signal'),('Signal, blocked request','blocked'),('Request, no fill','unfilled'),
 ('Positive net %','profitable_pct'),('Positive score %','positive_score_pct'),
 ('Score $','score'),('Net profit $','net_pnl'),('Sum session drawdown $','drawdown'),
 ('Gross losses $','gross_losses'),('Before commissions $','before_fees'),('Commissions $','fees'),
 ('Closed positions','closed_positions'),('Win rate %','win_rate_pct'),('Sessions traded','sessions_traded'),('Profitable sessions','profitable_sessions'),
 ('Top day positive-profit share','top_day_share'),('Top ticker-date profit share','top_ticker_share'),
 ('Worst session net $','worst_session'),('Worst session drawdown $','worst_drawdown'),
 ('Median position duration (s)','median_holding_seconds'),('Nodes, active candidates','active_nodes'),('Edges, active candidates','active_edges'),
 ('Unique genomes','unique_genomes'),('Duplicate genomes','duplicate_genomes'),('Distinct trade ledgers','distinct_behaviors'),('Largest parent lineage %','largest_parent_lineage_pct'),
 ('Comparable offspring','parent_comparisons'),('Beat best parent %','beat_parent_pct'),('Median parent improvement $','parent_improvement'),
 ('Selected in next population','retained'),('Unchanged offspring genomes','no_change_offspring'),
 ('GPU-active hours / efficiency','gpu_hours'),('Traded sessions: count bins','traded_sessions_histogram'),('Profitable sessions: count bins','profitable_sessions_histogram')]


def rows(model,history,generation,view):
    report=view.get('report',{});role=report.get('roles',{}).get(model.quality_role,{})
    if model.view=='quality_candidates':
        values=view.get('rows',[]);known=[r for r in values if r.get(model.quality_sort) is not None];missing=[r for r in values if r.get(model.quality_sort) is None]
        return sorted(known,key=lambda r:r[model.quality_sort],reverse=not model.quality_reverse)+missing
    if model.view=='quality_detail':
        detail=model.quality.candidate(generation,model.quality_role,model.quality_serial)
        return [dict(session=k,**v) for k,v in sorted(detail.get('sessions',{}).items())]
    if model.quality_page==1:return METRICS
    if model.quality_page==2:return history
    if model.quality_page==3:
        labels=['train_net_per_session','validation_net_per_session','train_score_per_session','validation_score_per_session',
                'train_profitable_session_pct','validation_profitable_session_pct','validation_worst_session','validation_drawdown',
                'validation_top_day_share','validation_inactive_pct']
        return [(k,[v.get(k) for v in report.get('generalization',{}).get('leaders',[])]) for k in labels]
    if model.quality_page==4:
        values=[r for r in role.get('groups',[]) if r['group'].startswith(model.quality_category+':')]
        field={'score':'score','net_pnl':'net_pnl','drawdown':'drawdown','closed_positions':'closed_positions'}.get(model.quality_sort,'score')
        return sorted(values,key=lambda r:(r.get(field,{}).get('median') or 0,r['group']),reverse=not model.quality_reverse)
    from .campaign_progress import diagnostics
    timing=report.get('timings',{})
    result=[('Full generation wall seconds',fmt(timing.get('full_generation_seconds'))),('Critical path seconds',fmt(timing.get('critical_path_seconds'))),('Replay worker seconds (not GPU time)',fmt(report.get('replay_worker_seconds')))]
    previous=next((r for r in history if r['generation']==generation-1),{})
    prior=previous.get('roles',{}).get(model.quality_role,{}).get('cohorts',{}).get('all',{})
    current=role.get('cohorts',{}).get('all',{});seconds=timing.get('full_generation_seconds')
    gain=(current['score']['median']-prior['score']['median'])*3600/seconds if current.get('complete') and prior.get('complete') and seconds and report.get('sealed') and previous.get('sealed') else None
    result.append(('Median score gain per wall hour',fmt(gain)))
    return result+diagnostics(report.get('reproduction',{}))


def key(model,key,height):
    setup(model)
    if key in ('g','a','h','back','d'):return False
    history,generation,view=context(model)
    if key in ('1','2','3','4','5'):
        model.quality_page=int(key);model.view='quality';model.selection=0;model.quality_group=None
        if key=='4':model.quality_cohort='all'
        return True
    if key=='t':model.quality_role='validation' if model.quality_role=='training' else 'training';model.selection=0;return True
    if key=='i':model.quality_generation=0;model.selection=0;return True
    if key=='c':model.quality_generation=None;model.selection=0;return True
    if key=='x':
        if model.view=='quality' and model.quality_page==4:return True
        choices=['all','offspring','survivors'];model.quality_cohort=choices[(choices.index(model.quality_cohort)+1)%3];model.selection=0;return True
    if key=='f':
        choices=['origin','family','operator','lineage'];model.quality_category=choices[(choices.index(model.quality_category)+1)%4];model.selection=0;return True
    if key in ('[',']'):
        gens=[r['generation'] for r in history];index=gens.index(generation) if generation in gens else 0
        if gens:model.quality_generation=gens[max(0,min(len(gens)-1,index+(-1 if key=='[' else 1)))];model.selection=0
        return True
    if key=='s':
        fields=['score','net_pnl','drawdown','closed_positions','sessions_traded','profitable_sessions'];model.quality_sort=fields[(fields.index(model.quality_sort)+1)%len(fields)];model.selection=0;return True
    if key=='r':model.quality_reverse=not model.quality_reverse;return True
    if key=='z':
        choices=['median','p90','best'];model.quality_stat=choices[(choices.index(model.quality_stat)+1)%3];return True
    values=rows(model,history,generation,view)
    if key in ('j','k','n','b','home','end'):
        step=max(1,height-11);delta={'j':1,'k':-1,'n':step,'b':-step,'home':-len(values),'end':len(values)}[key]
        model.selection=max(0,min(max(0,len(values)-1),model.selection+delta));return True
    if key in ('\r','\n') and values:
        selected=values[min(model.selection,len(values)-1)]
        if model.view=='quality_detail':
            model.remember();model.selected=model.quality.candidate(generation,model.quality_role,model.quality_serial)
            model.day=None;model.initial_mode=False;model.aggregate_mode=False;model.view='positions';model.selection=0;model.sort='net_pnl';model.open_positions(selected['session'])
        elif model.view=='quality_candidates':
            model.remember();model.quality_serial=selected['serial'];model.view='quality_detail';model.selection=0
        elif model.quality_page==4:
            model.remember();model.quality_group=selected['group'];model.view='quality_candidates';model.selection=0
        elif model.quality_page==2:
            model.quality_generation=selected['generation'];model.quality_page=1;model.selection=0
        elif model.quality_page==1:
            model.remember();model.quality_group=None;model.view='quality_candidates';model.selection=0
        return True
    return True


def render(model,width,height):
    history,generation,view=context(model);report=view.get('report',{});role=report.get('roles',{}).get(model.quality_role,{})
    coverage=len(role.get('days',[]));total=len(model.job[model.quality_role+'_days']);complete=coverage==total
    names={1:'POPULATION QUALITY',2:'GENERATION HISTORY',3:'GENERALIZATION',4:'GROUP QUALITY',5:'OPERATOR ATTEMPTS / REPRODUCTION'}
    title='CANDIDATES' if model.view=='quality_candidates' else 'CANDIDATE SESSIONS' if model.view=='quality_detail' else names[model.quality_page]
    layout=Layout();layout.split_column(Layout(name='head',size=4),Layout(name='body'),Layout(name='keys',size=4))
    age=time.time()-model.quality.index.get('updated',time.time())
    header=Text(f'V60 | {title} | generation {generation} | {model.quality_role.upper()} | {model.quality_cohort}\n',style='bright_cyan')
    header.append(f'{coverage}/{total} sessions | '+('COMPLETE split' if complete else 'PARTIAL: totals are provisional')+(' | generation sealed' if report.get('sealed') else ' | generation unsealed')+'\n',style='bright_green' if complete else 'bright_yellow')
    header.append(f'Observer cache age {age:.0f}s | pending receipts {model.quality.index.get("pending","-")} | validation display-only\n')
    errors=model.quality.index.get('errors',[])+report.get('warnings',[])
    header.append(model.quality.error or ('; '.join(errors) if errors else 'Background aggregation; no additional replays. Compare matching, complete session coverage.'),style='bright_red' if model.quality.error or errors else 'dim')
    layout['head'].update(header)
    values=rows(model,history,generation,view);limit=max(1,height-11);start=model.selection//limit*limit
    table=Table(expand=True,padding=(0,1));compact=width<160
    add=lambda label,w,align='right':table.add_column(label,width=w,min_width=w,max_width=w,no_wrap=True,overflow='ellipsis',justify=align)
    selected='bold bright_yellow on blue';caption=''
    if model.view=='quality_detail':
        add('Session',23,'left')
        for label in ('Score $','Net $','DD $','Trades'):add(label,12)
        for i,r in enumerate(values[start:start+limit],start):table.add_row(r['session'],*(fmt(r.get(k)) for k in ('score','net_pnl','drawdown','closed_positions')),style=selected if i==model.selection else None)
        caption=f'Candidate {model.quality_serial} | {len(values)} completed sessions | Enter: positions and sortable order details'
    elif model.view=='quality_candidates':
        add('Serial',7)
        fields=[('Score $','score'),('Net $','net_pnl'),('DD $','drawdown'),('Days traded','sessions_traded')]
        if not compact:fields += [('Profit days','profitable_sessions'),('Other score','opposite_score'),('Other net','opposite_net'),('Nodes','nodes')]
        for label,k in fields:add(label,12 if k not in ('nodes','sessions_traded','profitable_sessions') else 10)
        for i,r in enumerate(values[start:start+limit],start):table.add_row(str(r['serial']),*(fmt(r.get(k)) for _,k in fields),style=selected if i==model.selection else None)
        caption=f'{model.quality_group or model.quality_cohort} | {len(values)} candidates | sort {model.quality_sort} | Enter: candidate sessions'
    elif model.quality_page==1:
        metric_width=min(32,max(20,width//3));add('Metric',metric_width,'left');cohorts=role.get('cohorts',{})
        initial=next((h.get('roles',{}).get(model.quality_role,{}).get('cohorts',{}).get('all',{}) for h in history if h['generation']==0),{})
        initial_label=f'Initial {initial.get("coverage",0)}/{total}'
        columns=[(initial_label,initial),(model.quality_cohort.title(),cohorts.get(model.quality_cohort,{}))] if compact else [(initial_label,initial),('All',cohorts.get('all',{})),('New offspring',cohorts.get('offspring',{})),('Survivors',cohorts.get('survivors',{}))]
        for name,_ in columns:add(name,min(24,(width-metric_width-3*len(columns)-6)//len(columns)))
        for i,(label,k) in enumerate(values[start:start+limit],start):
            cells=[]
            for _,s in columns:
                value=s.get(k)
                cells.append(', '.join(f'{a}:{b}' for a,b in value.items()) if k.endswith('histogram') and value else fmt(value.get(model.quality_stat)) if isinstance(value,dict) else fmt(value))
            table.add_row(label,*cells,style=selected if i==model.selection else None)
        caption=f'Distributions: {"maximum" if model.quality_stat=="best" else model.quality_stat} | Z: median / p90 / maximum | shares 0–1 | Enter: candidates'
    elif model.quality_page==2:
        fields=[('Gen',5),('Coverage',8),('Median score',11),('P90 score',10),('Best score',10),('Net+ %',7),('Idle %',7)]
        if not compact:fields += [('Best so far',14),('Delta median',14),('Beat parent %',14),('Replay worker s',15)]
        for label,w in fields:add(label,w)
        best=None;prior=None;display=[]
        for h in values:
            rr=h.get('roles',{}).get(model.quality_role,{});s=rr.get('cohorts',{}).get('all',{});score=s.get('score',{})
            ready=s.get('complete') and h.get('sealed');median=score.get('median');delta=median-prior if ready and prior is not None else None
            if ready:best=max(best,score['best']) if best is not None else score['best'];prior=median
            cells=[str(h['generation'])+(' *' if not ready else ''),f'{len(rr.get("days",[]))}/{rr.get("total",total)}',fmt(median),fmt(score.get('p90')),fmt(score.get('best')),fmt(s.get('profitable_pct')),fmt(s.get('inactive_pct'))]
            if not compact:cells += [fmt(best),fmt(delta),fmt(rr.get('cohorts',{}).get('offspring',{}).get('beat_parent_pct')),fmt(h.get('replay_worker_seconds'))]
            display.append(cells)
        for i,cells in enumerate(display[start:start+limit],start):table.add_row(*cells,style=selected if i==model.selection else None)
        caption='* partial/unsealed; excluded from best-so-far and deltas | Enter: generation | replay worker time includes concurrent lane waiting'
    elif model.quality_page==3:
        add('Training-selected candidates',min(38,max(24,width-62)),'left')
        for label in ('Top 1','Top 10','Top 100'):add(label,min(16,max(10,(width-46)//3)))
        general=report.get('generalization',{})
        for i,(label,vals) in enumerate(values[start:start+limit],start):table.add_row(label.replace('_',' '),*([fmt(v) for v in vals] if vals else ['-','-','-']),style=selected if i==model.selection else None)
        caption=f'Score rank correlation {fmt(general.get("rank_correlation"))} | train-positive → val-negative {fmt(general.get("positive_to_negative_pct"))}% | '+('complete paired splits' if general.get('complete') else 'provisional / awaiting validation')
    elif model.quality_page==4:
        add('Group',min(32,max(18,width-62)),'left');fields=[('Count','count',7),('Idle %','inactive_pct',7),('Net+ %','profitable_pct',7),('Median score','score',12)]
        if not compact:fields += [('Median net','net_pnl',14),('Median DD','drawdown',14),('Beat parent %','beat_parent_pct',14),('Next pop','retained',10)]
        for label,k,w in fields:add(label,w)
        for i,r in enumerate(values[start:start+limit],start):table.add_row(r['group'],*[fmt(r.get(k,{}).get('median')) if isinstance(r.get(k),dict) else fmt(r.get(k)) for _,k,_ in fields],style=selected if i==model.selection else None)
        caption='F: origin / family / operator / parent lineage | overlapping groups; do not sum counts | Enter: group candidates'
    else:
        add('Recorded reproduction metric',max(32,width-30),'left');add('Value',20)
        for i,(label,value) in enumerate(values[start:start+limit],start):table.add_row(label,value,style=selected if i==model.selection else None)
        caption='Generation timing and next-population operator attempts; missing measurements remain unavailable'
    if not values:table.add_row(*(['Waiting for completed replay evidence']+['-']*(len(table.columns)-1)))
    layout['body'].update(Panel(table,title=title,subtitle=caption,padding=0))
    keys='1 Population | 2 Generations | 3 Generalization | 4 Groups | 5 Attempts\nT train/validation | I initial | C latest | [ / ] generation | X cohort\nArrows | N/B page | S sort | R reverse | F group | Z statistic | Enter open\nEsc back | G sessions | A all global | Q close (optimizer continues)'
    layout['keys'].update(Text(keys));return layout
