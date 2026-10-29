"""Observer-only totals for one aligned, explicitly covered replay split."""
import numpy as np
from .evolution_report import summarize

def combined(data,records,total,role='training'):
    if role not in ('training','validation'):raise ValueError('Unknown global split')
    scope='global-'+role
    sessions=[v for k,v in sorted(data['panels'].items()) if k.startswith(role+':')]
    if not sessions:return dict(generation=data['generation'],candidates=[],groups=[],coverage=0,total=total,scope=scope,partial=True)
    ids=sessions[0]['ids']
    if any(list(v['ids'])!=list(ids) for v in sessions):raise ValueError('Global observer population alignment changed')
    from .robust_score import combine
    panel=combine([v['panel'] for v in sessions])
    worst=np.maximum.reduce([v['panel']['drawdown'] for v in sessions])
    report=summarize(ids,panel,records,generation=data['generation'])
    for i,row in enumerate(report['candidates']):
        trades=panel['closed_positions'][i]
        row.update(winning_positions=float(panel['winning_positions'][i]),win_rate=100*panel['winning_positions'][i]/trades if trades else None,worst_drawdown=float(worst[i]),coverage=len(sessions),metric_scope=scope)
    report.update(coverage=len(sessions),total=total,scope=scope,partial=len(sessions)<total)
    other='validation' if role=='training' else 'training'
    opposite=[v for k,v in sorted(data['panels'].items()) if k.startswith(other+':')]
    if opposite:
        if any(list(v['ids'])!=list(ids) for v in opposite):raise ValueError('Opposite-split IDs differ')
        other_panel=combine([v['panel'] for v in opposite])
        for i,row in enumerate(report['candidates']):
            row.update(opposite_score=float(other_panel['score'][i]),opposite_net=float(other_panel['net_pnl'][i]),opposite_drawdown=float(other_panel['drawdown'][i]),opposite_coverage=len(opposite))
    return report
