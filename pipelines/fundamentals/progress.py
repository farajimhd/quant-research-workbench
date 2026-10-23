"""Durable batch progress and deliberately conservative, family-aware estimates."""
from datetime import datetime, timezone
from statistics import median
import textwrap


def duration(seconds):
    if seconds is None:return 'estimating'
    seconds=max(0,int(seconds))
    if seconds>=86400:return f'{seconds/86400:.1f}d'
    if seconds>=3600:return f'{seconds/3600:.1f}h'
    if seconds>=60:return f'{seconds/60:.1f}m'
    return f'{seconds}s'


def estimate(state):
    """No extrapolation from sparse families or metadata reuse to new writes."""
    if state['state']!='running':return None
    work=[]
    for family,remaining in state.get('remaining_by_family',{}).items():
        if not remaining:continue
        samples=state.get('samples',{}).get(family,{})
        # Reuse proportions of unknown pending work cannot be predicted safely.
        # New-work timing is an explicitly conservative estimate on resumes.
        values=samples.get('published',[])
        if len(values)<3:return None
        work.append((remaining,median(values)))
    if not work:return None if state.get('queued',0) else 0
    lanes=max(1,state.get('workers',1))
    archive_seconds=0
    if state.get('archive_remaining',0):
        archive_samples=state.get('archive_samples',[])
        if len(archive_samples)<3:return None
        archive_seconds=state['archive_remaining']*median(archive_samples)
    return max(sum(n*t for n,t in work)/lanes,max(t for _,t in work))+archive_seconds


def render(state,width=80,height=24,now=None):
    width=max(20,width);height=max(10,height)
    c=state.get('counts',{})
    done=sum(c.get(k,0) for k in ('published','unchanged','audited'))
    total=state.get('total',done+state.get('queued',0)+len(state.get('active',{}))+c.get('failed',0))
    current=state['state'];stamp=state.get('updated_at')
    age=None
    if stamp:
        age=max(0,((now or datetime.now(timezone.utc))-datetime.fromisoformat(stamp)).total_seconds())
        if age>45 and current in ('running','planning'):current+=' | STALE'
    barwidth=max(8,min(32,width-18));filled=int(barwidth*done/total) if total else 0
    bar='['+'#'*filled+'-'*(barwidth-filled)+']'
    lines=[f"Fundamentals | {current} | {state.get('mode','backfill')}",
        f"{bar} {done:,}/{total:,} {state.get('unit','batches')}" if total else 'Planning scope; total not yet known',
        f"{c.get('published',0):,} published | {c.get('unchanged',0):,} unchanged | {c.get('audited',0):,} audited",
        f"Active {len(state.get('active',{}))} | queued {max(0,state.get('queued',0)-(sum(k!='archive' for k in state.get('active',{})) if state.get('counts_include_active') else 0)):,} | failed {c.get('failed',0)} | retries 0",
        f"Blocked {state.get('blocked',0)} | exclusions {state.get('exclusions',0)}",
        f"Elapsed {duration(state.get('elapsed',0))} | ETA {('finished' if current=='complete' else 'blocked' if current in ('blocked','failed','stopped') else duration(None if age and age>45 else estimate(state)))}",
        'ETA uses new-work family samples; reuse is counted separately.',
        f"New facts {state.get('new_rows',0):,} | reused facts {state.get('reused_rows',0):,}",
        state.get('qualification_status','Retained observations; not public-time certified.')]
    if state.get('error'):lines.insert(1,'ERROR: '+state['error'])
    active=list(state.get('active',{}).values())
    room=max(0,height-len(lines)-2)
    for worker in active[:room]:
        lines.append(f"{worker['family']} | {worker['stage']} | {duration(state.get('elapsed',0)-worker.get('started',state.get('elapsed',0)))}")
    if len(active)>room:lines.append(f'{len(active)-room} additional workers; see status.json')
    if age is not None:lines.append(f'Status age {age:.0f}s | timestamps UTC')
    wrapped=[part for line in lines for part in textwrap.wrap(line,width=width)]
    if len(wrapped)>height:wrapped=wrapped[:height-1]+['More detail in status.json']
    return '\n'.join(wrapped)
