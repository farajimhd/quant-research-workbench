"""Phase-aware V48 observer: owned ticker groups and comparable scores."""
from statistics import median
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress,TextColumn,BarColumn,TaskProgressColumn
from rich.table import Column
from .observer_model import SORTS,filtered,ordered,grouped


def page_size(height):return max(1,height-(21 if height>=32 else 17))


def visible(data,settings):
    rows=filtered(data,settings.get('case'),settings.get('eligible',False),settings.get('active',False))
    if settings.get('grouped'):rows=grouped(rows,data.get('next_quotas'))
    return ordered(rows,settings.get('sort','score'),settings.get('reverse',False))


def num(value):return '-' if value is None else f'{value:,.2f}'


def render(data,width,height,settings,selection=0,detail='financial',stage='entry',detail_page=0,rule_lines=None):
    compact=width<105;short=height<32;phase=data.get('phase','?');p=data.get('session_progress',{})
    layout=Layout();layout.split_column(Layout(name='header',size=2),Layout(name='activity',size=3),
        Layout(name='summary',size=2),Layout(name='ranking'),Layout(name='details',size=3 if short else 7),Layout(name='keys',size=3))
    status=p.get('status','starting') if data.get('running') else 'STOPPED'
    label='ISOLATED TICKER/SESSION' if phase==0 else 'MIXED SESSION'
    header=f"V48 | PHASE {phase} {label} | {data.get('day','')} | {status.upper()}\nGeneration {p.get('generation',1)}/{data.get('target',30)} | {data.get('generation',0)} sealed | {p.get('stage','Waiting for status')}"
    layout['header'].update(Text(header,style='cyan'))
    bars=Progress(TextColumn('{task.description}',table_column=Column(width=13,no_wrap=True)),
        BarColumn(bar_width=None,style='grey23',complete_style='cyan',finished_style='green',pulse_style='magenta'),
        TaskProgressColumn(),TextColumn('{task.fields[units]}',table_column=Column(width=15,no_wrap=True)),expand=True)
    for label,done,total in (('Generations',data.get('generation',0),data.get('target',30)),
                            ('Batches',p.get('completed_local_batches',0),p.get('total_local_batches',0)),
                            ('Replay clocks',p.get('completed_seconds',0),p.get('total_seconds',0))):
        bars.add_task(label,total=total or None,completed=done,units=f'{done:,}/{total:,}' if total else 'waiting')
    layout['activity'].update(bars)
    source=filtered(data,settings.get('case'),settings.get('eligible',False),settings.get('active',False))
    valid=[r for r in source if r['eligible']];scope=data.get('case_labels',{}).get(settings.get('case'),'ALL TICKERS' if phase==0 else 'FULL SESSION')
    if settings.get('eligible'):scope+=' [E-only]'
    if settings.get('active'):scope+=' [active]'
    best=max((r['score'] for r in valid),default=None);med=median([r['score'] for r in valid]) if valid else None
    summary=f"{scope} | {len(source):,} candidates | {len(valid):,} eligible | {sum(r['score']>0 for r in valid):,} positive | best S {num(best)}"
    if compact:summary=f"{scope} | N {len(source):,} | E {len(valid):,} | best S {num(best)}"
    if not data.get('display_ready',True):summary=f"Reading sealed generation {data.get('generation',0)} score panel..."
    note=data.get('display_error')
    if not note:
        if settings.get('grouped'):
            total=sum(g['score'] for g in grouped(source) if g['score'] is not None)
            groups=grouped(source);positive=sum(g['score'] is not None and g['score']>0 for g in groups)
            note=f'{positive}/{len(groups)} positive tickers | best-score sum {num(total)}; diagnostic, not portfolio.'
        elif phase==0:note=f'Owned-case scores only; no combined portfolio. Median eligible S {num(med)}.'
        else:note='Session score includes every ticker; one selection pool. Loss penalty = 0.5 x losing dollars.'
    layout['summary'].update(Text(summary+'\n'+note,style='yellow' if data.get('display_error') else None))
    rows=visible(data,settings);size=page_size(height);selection=min(selection,max(0,len(rows)-1));start=selection//size*size
    table=Table(expand=True,padding=(0,0),show_edge=False);grouped_view=settings.get('grouped',False)
    if grouped_view:
        columns=['#','Ticker','N/E','Next','Best S $','Median S $','Best net $']
        if not compact:columns+=['Loss* $','DD* $']
    else:
        columns=['#','Ticker','Serial','Score $','Net $','Loss $']
        if not compact:columns+=['Real DD $','Win %','Pos','E']
        else:columns+=['Pos','E']
    for c in columns:table.add_column(c,justify='left' if c in ('Ticker','Serial') else 'right',no_wrap=True,overflow='ellipsis')
    for i,r in enumerate(rows[start:start+size],start):
        m=r['metrics']
        if grouped_view:
            values=[str(i+1),r['ticker'],f"{r['count']}/{r['eligible_count']}",str(r['next_slots']) if r['next_slots'] is not None else '-',num(r['score']),num(r['median_score']),num(m['net_pnl'])]
            if not compact:values+=[num(r['gross_losses']),num(r['realized_drawdown'])]
        else:
            values=[str(i+1),r['ticker'],str(r['serial']),num(r['score']),num(m['net_pnl']),num(r['gross_losses'])]
            if not compact:values+=[num(r['realized_drawdown']),'-' if m['win_rate'] is None else f"{100*m['win_rate']:.1f}",str(int(m['closed_positions'])),'Y' if r['eligible'] else 'N']
            else:values+=[str(int(m['closed_positions'])),'Y' if r['eligible'] else 'N']
        table.add_row(*values,style='bold cyan' if i==selection else None)
    sort=next(x for x in SORTS.values() if x[0]==settings.get('sort','score'));ascending=sort[2]!=settings.get('reverse',False)
    title=f"{'TICKER SUMMARY' if grouped_view else 'CANDIDATES'} | {sort[1]} {'low first' if ascending else 'high first'} | {start+1 if rows else 0}-{min(start+size,len(rows))}/{len(rows)}"
    layout['ranking'].update(Panel(table,title=title,padding=0))
    selected=rows[selection] if rows else None
    if not selected:lines=['Loading the sealed score panel; no empty-result claim yet.' if not data.get('display_ready',True) else 'Awaiting the first sealed ranking.' if not data.get('generation') else 'No candidates match. Press 0 for all tickers; Z/C toggle eligibility/activity filters.']
    elif grouped_view:
        lines=[f"{selected['ticker']}: {selected['count']} candidates; {selected['eligible_count']} eligible; {selected['positive_count']} positive.",
               f"Best eligible serial {selected['best_serial'] if selected['best_serial'] is not None else '-'} | next-generation slots {selected['next_slots'] if selected['next_slots'] is not None else '-'}.",
               'Enter: inspect ticker. Loss/DD/Win/Risk use best-score eligible candidate; best net may be another.']
    elif detail=='rules' and rule_lines:
        payload=data.get('payloads',{}).get(selected['serial']);lines=rule_lines(payload,stage)
        capacity=max(1,(3 if short else 7)-2);begin=detail_page*capacity;lines=lines[begin:begin+capacity] or ['No more lines. U returns to the previous page.']
    elif detail=='policy':
        payload=data.get('payloads',{}).get(selected['serial'])
        if payload:
            pol=payload['policy'];lines=[f"Serial {selected['serial']} | {selected['ticker']} | owner {selected['case_owner'] or 'none: mixed session'}",
                f"Stop {100*pol['stop_fraction']:.2f}% | target {100*pol['target_fraction']:.2f}% | trail {100*pol['trail_fraction']:.2f}%",
                f"Maximum adds {pol['maximum_adds']} | cooldown {pol['cooldown']} seconds | bid/ask fills."]
        else:lines=['Historical genome not retained in display cache; sealed score and receipt remain available.']
    else:
        m=selected['metrics'];loss=selected['gross_losses'];dd=selected['realized_drawdown']
        lines=[f"{selected['ticker']} / {selected['serial']} | S {num(selected['score'])} | Net {num(m['net_pnl'])} | E {'YES' if selected['eligible'] else 'NO'}",
            f"DD {num(dd)} x 0.05 = {num(.05*dd)}; gross losses {num(loss)} x 0.5 = {num(.5*loss)}",
            f"Positions {int(m['closed_positions'])} | stop risk {num(m['maximum_position_risk'])} | terminal {'closed' if m.get('terminal_valid',True) else 'NOT CLOSED'} | C filters inactive",
            f"Owner: {selected['case_owner'] or 'unrestricted mixed-session strategy'}",
            'S = net - 0.05 x DD - 0.5 x losses, after fees.']
    layout['details'].update(Panel(Text('\n'.join(lines)),title='READ-ONLY DETAILS',padding=0))
    keys=('1 Score  2 Net  3 Loss  4 DD  5 Win  6 Positions  7 Risk | V reverse\n'
          'G ticker summary | [ ] ticker | 0 all | Enter inspect | Z eligible | C active\n'
          'J/K select N/B page | F/O score P policy E/X/A/D/T rules U/I lines | Q close')
    if compact:keys=('1 Score 2 Net 3 Loss 4 DD 5 Win 6 Pos 7 Risk | V reverse\n'
                     'G groups [ ] ticker 0 all Enter inspect | Z eligible C active\n'
                     'J/K rows N/B page F/O/P details E/X/A/D/T rules U/I | Q close')
    layout['keys'].update(Text(keys))
    return layout
