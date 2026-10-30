"""Bounded background loading of immutable phase-one exports on either host."""
import threading
from .progress_sessions import read_verified

class Archive:
    def __init__(self,bus,days,stop,require_nodes=False):
        self.data={};self.errors={};self.stop=stop
        def load():
            from .full_common import ranking
            from .evolution_report import summarize
            for day in days:
                if stop.is_set():return
                try:
                    folder=bus/'sessions'/day
                    if not (folder/'export.json').exists():continue
                    meta=read_verified(folder/'export.json')
                    if meta['day']!=day:raise ValueError('Archive day mismatch')
                    train=read_verified(folder/'training.json',meta['files']['training.json'])
                    import numpy as np
                    train['panel']={k:np.asarray(v) for k,v in train['panel'].items()}
                    records=read_verified(folder/'pedigree.json',meta['files']['pedigree.json'])
                    seal=read_verified(folder/'seal.json',meta['files']['seal.json'])
                    sidecar=bus/'observer'/(day+'.json');nodes={}
                    if sidecar.exists():
                        index=read_verified(sidecar)
                        from research.vectorized_backtest.v42.distributed_assets import sha
                        if index['export_sha256']!=sha(folder/'export.json'):raise ValueError('Observer node index export mismatch')
                        nodes=index['nodes']
                    for r in records:
                        r['source_sessions']=[day]
                        if str(r['serial']) in nodes:r['node_count']=nodes[str(r['serial'])]
                    self.data[day]=dict(status=dict(status='complete',stage='Sealed phase-1 export',completed_generations=50,ranking_generation=50,training_ranking=ranking(train['panel'],train['ids'])),report=summarize(train['ids'],train['panel'],records,generation=50),last_seconds=seal.get('wall_seconds'))
                except Exception as error:self.errors[day]=str(error)
            pending={day for day,value in self.data.items() if any(r['nodes'] is None for r in value['report']['candidates'])} if require_nodes else set()
            while pending and not stop.is_set():
                for day in list(pending):
                    path=bus/'observer'/(day+'.json')
                    if not path.exists():continue
                    try:
                        from research.vectorized_backtest.v42.distributed_assets import sha
                        index=read_verified(path)
                        if index['export_sha256']!=sha(bus/'sessions'/day/'export.json'):raise ValueError('Observer node index export mismatch')
                        report=self.data[day]['report']
                        for row in report['candidates']:row['nodes']=index['nodes'][str(row['serial'])]
                        import statistics
                        fields={'operator':'operators','family':'founder_groups','founder-method':'founder_methods','origin-session':'source_sessions','immigration':'immigration_categories'}
                        for group in report['groups']:
                            values=[r['nodes'] for r in report['candidates'] if group['group'] in r[fields[group['category']]]]
                            group['median_nodes']=statistics.median(values) if values else None
                        pending.remove(day)
                    except Exception as error:self.errors[day]=str(error);pending.remove(day)
                stop.wait(1)
        self.thread=threading.Thread(target=load,daemon=True);self.thread.start()
