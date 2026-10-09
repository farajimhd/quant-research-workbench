"""Reporting metrics only: no hidden contribution to search fitness."""
import math
import torch

def sharpe(returns,*,annualization=252.,risk_free_per_session=0.):
    """[session,candidate], sample SD. Undefined ratios remain NaN internally.

    Final JSON uses null. Annualization assumes representative daily sessions;
    selected historical sessions do not establish a full-year return process.
    """
    if returns.ndim!=2 or not torch.isfinite(returns).all() or annualization<=0:
        raise ValueError('Finite daily returns and positive annualization required')
    if len(returns)<2:return torch.full((returns.shape[1],),float('nan'),device=returns.device,dtype=returns.dtype)
    excess=returns-risk_free_per_session;std=excess.std(0,correction=1)
    return torch.where(std>0,excess.mean(0)/std*math.sqrt(annualization),float('nan'))

def financial_metrics(results,*,initial_cash=10000.):
    def values(name):return torch.stack([torch.as_tensor(r[name],dtype=torch.float64) for r in results])
    pnl=values('net_pnl');r=pnl/initial_cash
    ordered=r.sort(0).values
    trades={}
    durations={}
    position_tail={}
    if all('closed_position_pnl_samples' in result for result in results):
        from .position_tail import tail_summary
        summaries=[tail_summary([sample for result in results for sample in result['closed_position_pnl_samples'][lane]]) for lane in range(pnl.shape[1])]
        position_tail={name:torch.tensor([row[name] if row[name] is not None else float('nan') for row in summaries],dtype=torch.float64) for name in summaries[0]}
    if all('closed_position_duration_samples' in result for result in results):
        from .position_metrics import duration_summary
        summaries=[duration_summary([sample for result in results for sample in result['closed_position_duration_samples'][lane]]) for lane in range(pnl.shape[1])]
        durations={name:torch.tensor([row[name] if row[name] is not None else float('nan') for row in summaries],dtype=torch.float64) for name in summaries[0]}
    if all('closed_positions' in result for result in results):
        closed=values('closed_positions').sum(0);wins=values('winning_positions').sum(0);losses=values('losing_positions').sum(0)
        profit=values('gross_profit').sum(0);loss=values('gross_loss').sum(0)
        trades=dict(closed_positions=closed,winning_positions=wins,losing_positions=losses,
                    position_win_rate=torch.where(closed>0,wins/closed,float('nan')),
                    profit_factor=torch.where(loss>0,profit/loss,float('nan')),gross_profit=profit,gross_loss=loss)
    return dict(**trades,**durations,**position_tail,sharpe_daily=sharpe(r,annualization=1),sharpe_annualized_estimate=sharpe(r),
        sharpe_ex_best_annualized_estimate=sharpe(ordered[:-1]),sharpe_sessions=len(r),sharpe_risk_free_per_session=0.,
        total_pnl=pnl.sum(0),worst_pnl=pnl.amin(0),worst_drawdown=values('drawdown').amax(0),
        batches=values('filled_batches').sum(0),positions=values('positions_opened').sum(0),fills=values('fill_count').sum(0),
        open=values('open_positions').sum(0),mean_hold_seconds=values('sold_share_seconds').sum(0)/values('sold_shares').sum(0).clamp_min(1),
        stop_risk_hours=values('stop_risk_dollar_seconds').sum(0)/(initial_cash*3600),capital_hours=values('capital_dollar_seconds').sum(0)/(initial_cash*3600))
