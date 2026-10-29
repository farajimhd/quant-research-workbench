"""Observer-only access to the rescored founder cohort, independent of evolution."""
import threading
from pathlib import Path
import numpy as np
from .progress_sessions import read_verified


class InitialData:
    def __init__(self,job,root,stop):
        self.job=job;self.root=Path(root);self.stop=stop;self.data=None;self.error=None
        self.thread=None;self.session_thread=None;self.request=None;self.sessions=[]

    def path(self,path):
        from research.vectorized_backtest.v42.distributed_assets import remote
        return remote(path,self.job['workstation_share']) if str(self.root).startswith('\\\\') and not str(path).startswith('\\\\') else Path(path)

    def report(self):
        if self.data is None and self.thread is None:
            self.thread=threading.Thread(target=self.load,daemon=True);self.thread.start()
        return self.data or dict(generation=0,candidates=[],groups=[],coverage=0,total=24,partial=True,scope='initial-founders',error=self.error)

    def load(self):
        try:
            from .evolution_report import summarize
            binding=self.job['founder_restart']
            manifest=read_verified(self.path(binding['manifest']),binding['sha256'])
            seal=read_verified(self.path(manifest['seal']),manifest['seal_sha256'])
            source=self.path(manifest['training']).parent
            records=read_verified(source/'evaluated-pedigree.json',seal['evaluated_pedigree_sha256'])
            evidence=read_verified(self.root/'founder-bootstrap/training.json')
            from .robust_score import VERSION,COEFFICIENT
            if evidence['score_version']!=VERSION or evidence['coefficient']!=COEFFICIENT:raise ValueError('Initial score version differs')
            if set(evidence['ids'])!={r['serial'] for r in records}:raise ValueError('Initial pedigree IDs differ')
            original=read_verified(self.path(manifest['training']),manifest['training_sha256'])
            if evidence['ids']!=original['ids'] or evidence['receipts']!=original['receipts']:raise ValueError('Initial receipt binding differs')
            panel={k:np.asarray(v) for k,v in evidence['panel'].items()}
            report=summarize(evidence['ids'],panel,[dict(r,global_founder=True) for r in records],generation=0)
            for i,row in enumerate(report['candidates']):
                trades=panel['closed_positions'][i]
                row.update(initial_founder=True,original_founder=True,win_rate=100*panel['winning_positions'][i]/trades if trades else None)
            report.update(coverage=len(evidence['receipts']),total=len(self.job['training_days']),partial=False,scope='initial-founders')
            self.evidence=evidence;self.source_root=source.parent.parent
            self.data=report
        except Exception as error:self.error=str(error)

    def candidate_sessions(self,serial):
        if self.data is None:self.report();return []
        if self.request!=serial:self.request=serial;self.sessions=[];self.error=None
        if self.session_thread is None or not self.session_thread.is_alive():
            if len(self.sessions)<len(self.evidence['receipts']):
                self.session_thread=threading.Thread(target=self.load_sessions,daemon=True);self.session_thread.start()
        return list(self.sessions)

    def load_sessions(self):
        from .full_common import aggregate
        serial=self.request;index=self.evidence['ids'].index(serial)
        try:
            complete={r['session'] for r in self.sessions}
            for day,ref in self.evidence['receipts'].items():
                if self.stop.is_set() or self.request!=serial:return
                key='training:'+day
                if key in complete:continue
                receipt=read_verified(self.path(ref['receipt']),ref['receipt_sha256'])
                if receipt['day']!=day or receipt['split']!='training' or receipt['validation_opened']:raise ValueError('Initial session scope differs')
                metrics={k:[v[index]] for k,v in receipt['metrics'].items()};del receipt
                panel=aggregate([metrics]);del metrics
                row=dict(session=key,**{k:float(panel[k][0]) for k in ('score','net_pnl','gross_losses','drawdown')})
                if self.request!=serial:return
                self.sessions=[*self.sessions,row]
        except Exception as error:self.error=str(error)
