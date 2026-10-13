"""Read-only observer data and sorting; never imported by the optimizer."""
import hashlib
import json
import threading
from pathlib import Path
import numpy as np

MODES=('session_score','net_profit','session_profit','training_rank')


class SortedRows:
    """Sort numeric indexes once; construct only the visible page's dictionaries."""
    def __init__(self,rows,days,scores,profits,active,mode,split):
        if mode not in MODES:raise ValueError('Unknown ranking sort')
        self.rows=rows;self.days=days;self.scores=scores;self.profits=profits
        self.session=mode in ('session_score','session_profit')
        candidate=np.asarray([r['candidate']-1 for r in rows],dtype=np.int64)
        serial=np.asarray([r['serial'] for r in rows],dtype=np.int64)
        if not self.session:
            if mode=='training_rank':
                self.order=np.argsort(np.asarray([r.get('training_rank',r['rank']) for r in rows]),kind='stable')
                return
            self.order=np.lexsort((np.asarray([r['rank'] for r in rows]),-np.asarray([r['metrics']['total_pnl'] for r in rows])))
            return
        d,c=np.indices((len(days),len(rows)))
        mask=active[:,candidate] if split=='training' else np.ones(d.shape,dtype=bool)
        d=d[mask];c=c[mask]
        local=np.lexsort((serial[c],-scores[d,candidate[c]],d))
        profit_order=np.lexsort((serial[c],-profits[d,candidate[c]],d))
        local_days=d[profit_order]
        starts=np.maximum.accumulate(np.where(np.r_[True,local_days[1:]!=local_days[:-1]],np.arange(len(local)),0)) if len(local) else np.empty(0,dtype=int)
        ranks=np.empty(len(local),dtype=int);ranks[profit_order]=np.arange(len(local))-starts+1
        order=local if mode=='session_score' else np.lexsort((serial[c],d,-profits[d,candidate[c]]))
        self.d=d[order];self.c=c[order];self.ranks=ranks[order];self.candidate=candidate

    def __len__(self):return len(self.d) if self.session else len(self.order)

    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if not self.session:return self.rows[int(self.order[index])]
        d=int(self.d[index]);c=int(self.c[index]);i=int(self.candidate[c])
        return dict(self.rows[c],session=str(self.days[d]),session_rank=int(self.ranks[index]),
                    session_score=float(self.scores[d,i]),session_profit=float(self.profits[d,i]))


def load_panel(output,split,generation):
    root=Path(output)/split/f'generation-{generation:04d}'
    seal=json.loads((root/'complete.json').read_text(encoding='utf-8'))
    path=root/'session-panel.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=seal['session_panel_sha256']:
        raise ValueError('Display session panel integrity mismatch')
    rows=json.loads((root/'ranking.json').read_text(encoding='utf-8'))
    with np.load(path,allow_pickle=False) as panel:
        days=panel['days'];scores=panel['scores'];profits=panel['profits'];active=panel['active']
    if scores.ndim!=2 or scores.shape!=profits.shape or active.shape!=scores.shape or len(days)!=len(scores) or not np.isfinite(scores).all() or not np.isfinite(profits).all():
        raise ValueError('Invalid display session panel')
    if any(not 1<=r['candidate']<=scores.shape[1] for r in rows):raise ValueError('Display candidate index mismatch')
    if list(days)!=sorted(seal['session_receipts']):raise ValueError('Display session dates mismatch')
    return rows,days,scores,profits,active


class RankingObserver:
    """Nonblocking UI snapshots; disk reads and sorts run in this observer thread."""
    def __init__(self,output):
        self.output=Path(output);self.request=('training','net_profit');self.snapshot={}
        self.cache={};self.stop=threading.Event();self.lock=threading.Lock()
        self.thread=threading.Thread(target=self._run,name='v16-display-loader',daemon=True)

    def start(self):self.thread.start();return self
    def close(self):self.stop.set();self.thread.join(timeout=2)
    def set_view(self,split,mode):
        if split not in ('training','validation') or mode not in MODES:raise ValueError('Unknown display mode')
        self.request=(split,mode)
    def read(self):
        with self.lock:return dict(self.snapshot)

    def refresh(self):
        request=self.request;split,mode=request
        state=json.loads((self.output/'status.json').read_text(encoding='utf-8'))
        if state.get('validation_feedback') is not False:raise ValueError('Invalid observer snapshot')
        generation=state.get('training_ranking_generation' if split=='training' else 'validation_generation',0)
        rows=[]
        if generation:
            cached=self.cache.get(split)
            if cached is None or cached[0]!=generation:
                cached=(generation,load_panel(self.output,split,generation),{})
                self.cache[split]=cached
            if mode not in cached[2]:cached[2][mode]=SortedRows(*cached[1],mode,split)
            rows=cached[2][mode]
        state.update(_display_rows=rows,_display_split=split,_display_sort=mode,_display_generation=generation)
        if request==self.request:
            with self.lock:self.snapshot=state

    def _run(self):
        while not self.stop.is_set():
            try:self.refresh()
            except (OSError,ValueError,KeyError) as error:
                with self.lock:self.snapshot={**self.snapshot,'display_error':str(error)}
            self.stop.wait(.5)
