"""Observer-only totals for an aligned, explicitly covered training population."""
import numpy as np
from .evolution_report import summarize

def combined(data,records,total):
    sessions=[v for k,v in sorted(data['panels'].items()) if k.startswith('training:')]
    if not sessions:return dict(generation=data['generation'],candidates=[],groups=[],coverage=0,total=total)
    ids=sessions[0]['ids']
    if any(list(v['ids'])!=list(ids) for v in sessions):raise ValueError('Global observer population alignment changed')
    keys=('score','net_pnl','gross_losses','drawdown','closed_positions','winning_positions')
    panel={k:sum((np.asarray(v['panel'].get(k,np.zeros(len(ids))),float) for v in sessions),np.zeros(len(ids))) for k in keys}
    panel['eligible']=np.logical_and.reduce([v['panel']['eligible'] for v in sessions])
    worst=np.maximum.reduce([v['panel']['drawdown'] for v in sessions])
    report=summarize(ids,panel,records,generation=data['generation'])
    for i,row in enumerate(report['candidates']):
        trades=panel['closed_positions'][i]
        row.update(winning_positions=float(panel['winning_positions'][i]),win_rate=100*panel['winning_positions'][i]/trades if trades else None,worst_drawdown=float(worst[i]),coverage=len(sessions),metric_scope='global-training')
    report.update(coverage=len(sessions),total=total,scope='global-training',partial=len(sessions)<total)
    return report
