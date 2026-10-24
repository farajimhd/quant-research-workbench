"""Read-only V48 display projections; never import runners or mutate receipts."""
from collections import defaultdict
import hashlib,json,math
from pathlib import Path
from statistics import median
from research.vectorized_backtest.v42.local_details import DetailObserver as BaseObserver

SORTS={'1':('score','Score',False),'2':('profit','Net P&L',False),
       '3':('loss','Gross loss',True),'4':('drawdown','Realized DD',True),
       '5':('win','Win rate',False),'6':('positions','Positions',False),
       '7':('risk','Stop risk',True)}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def ticker_labels(root,day):
    manifest=json.loads((Path(root)/'manifest.json').read_bytes())
    campaign=Path(manifest.get('parent_campaign',root))
    run=json.loads((campaign/'manifest.json').read_bytes());job_path=Path(run['job'])
    if sha(job_path)!=run['job_sha256']:raise ValueError('Display job identity changed')
    job=json.loads(job_path.read_bytes());training=Path(job['training'])
    if sha(training)!=job['training_sha256']:raise ValueError('Display training identity changed')
    data=json.loads(training.read_bytes());receipt=Path(data['inputs'])/day/'complete.json'
    if sha(receipt)!=data['receipts'][day]['inputs']:raise ValueError('Display ticker identity changed')
    rows=json.loads(receipt.read_bytes())['listings'];names=defaultdict(set)
    for row in rows:
        if row.get('ticker'):names[day+':'+row['security_id']].add(row['ticker'])
    return {case:'/'.join(sorted(tickers)) for case,tickers in names.items()}


def enrich(rows,scores,labels):
    d=scores['objective_diagnostics'];n=len(rows)
    columns=(scores['ids'],d['pnl'],d['gross_losses'],d['session_realized_drawdown'],d['case_owners'])
    if any(len(x)!=n for x in columns):raise ValueError('Display score/owner alignment changed')
    result=[]
    for row,serial,pnl,loss,dd,owner in zip(rows,*columns):
        if row['serial']!=serial:raise ValueError('Display serial alignment changed')
        if not all(math.isfinite(float(x)) for x in (pnl,loss,dd,row['score'])) or loss<0 or dd<0:raise ValueError('Invalid display score components')
        if not math.isclose(row['score'],pnl-.05*dd-.5*loss,rel_tol=1e-9,abs_tol=1e-6):raise ValueError('Display cost does not reconcile')
        result.append(dict(row,case_owner=owner,ticker=labels.get(owner,owner.rsplit(':',1)[-1] if owner else 'MIXED'),
                           gross_losses=float(loss),realized_drawdown=float(dd)))
    return result


def filtered(data,case=None,eligible=False,active=False):
    return [r for r in data.get('rows',[]) if (case is None or r['case_owner']==case)
            and (not eligible or r['eligible']) and (not active or r['metrics']['closed_positions']>0)]


def ordered(rows,mode='score',reverse=False):
    asc=next(x[2] for x in SORTS.values() if x[0]==mode)
    def value(r):
        m=r['metrics']
        return {'score':r['score'],'profit':m['net_pnl'],'loss':r['gross_losses'],
                'drawdown':r['realized_drawdown'],'win':m.get('win_rate'),
                'positions':m['closed_positions'],'risk':m['maximum_position_risk']}[mode]
    direction=1 if asc!=reverse else -1
    return sorted(rows,key=lambda r:(value(r) is None,direction*value(r) if value(r) is not None else 0,r['serial']))


def grouped(rows,quotas=None):
    groups=defaultdict(list)
    for r in rows:groups[r['case_owner']].append(r)
    out=[]
    for case,group in groups.items():
        valid=[r for r in group if r['eligible']];best=max(valid,key=lambda r:(r['score'],-r['serial'])) if valid else None
        summary=dict(case_owner=case,ticker=group[0]['ticker'],serial=min(r['serial'] for r in group),
                     count=len(group),eligible_count=len(valid),positive_count=sum(r['score']>0 for r in valid),
                     next_slots=(quotas or {}).get(case),best_serial=best['serial'] if best else None,
                     score=best['score'] if best else None,median_score=median([r['score'] for r in valid]) if valid else None,
                     gross_losses=best['gross_losses'] if best else None,realized_drawdown=best['realized_drawdown'] if best else None,
                     metrics=dict(net_pnl=max(r['metrics']['net_pnl'] for r in valid) if valid else None,
                                  win_rate=best['metrics']['win_rate'] if best else None,
                                  closed_positions=sum(r['metrics']['closed_positions'] for r in group),
                                  maximum_position_risk=best['metrics']['maximum_position_risk'] if best else None))
        out.append(summary)
    return out


class Observer(BaseObserver):
    def __init__(self,active):
        super().__init__(active);self.labels_cache={};self.sealed_cache={};self.display_snapshot={}

    def read(self):
        data=super().read()
        if not data:return data
        if 'phase' in data and all('gross_losses' in r for r in data.get('rows',[])):return data
        if self.display_snapshot:return dict(self.display_snapshot,display_error=data.get('display_error','Reading the next sealed score panel'))
        return dict(data,rows=[],phase=data.get('session_progress',{}).get('phase'),case_labels={},next_quotas={})

    def refresh(self):
        super().refresh()
        with self.lock:data=dict(self.data)
        if not data or not data.get('day'):return
        root=Path(data['root']);day=data['day'];key=(str(root),day)
        if key not in self.labels_cache:self.labels_cache[key]=ticker_labels(root,day)
        generation=data.get('generation',0);rows=[];quotas={}
        if generation:
            cache_key=(*key,generation)
            if cache_key not in self.sealed_cache:
                scores=json.loads((root/'local'/day/f'generation-{generation:04d}'/'scores.json').read_bytes())
                rows=enrich(data['rows'],scores,self.labels_cache[key]);quotas=scores['diversity'].get('case_quotas',{})
                self.sealed_cache[cache_key]=(rows,quotas)
                while len(self.sealed_cache)>2:self.sealed_cache.pop(next(iter(self.sealed_cache)))
            rows,quotas=self.sealed_cache[cache_key]
        phase=data.get('session_progress',{}).get('phase',json.loads((root/'manifest.json').read_bytes()).get('phase'))
        data.update(rows=rows,case_labels=self.labels_cache[key],next_quotas=quotas,phase=phase)
        with self.lock:self.data=data;self.display_snapshot=data
