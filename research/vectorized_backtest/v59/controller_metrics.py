"""Keep selection inputs in typed buffers, not millions of Python ledger scalars.

Full receipts remain untouched in the publication lane. Conversion happens per
completed session while other sessions replay, rather than at generation cleanup.
"""
import numpy as np

SCALARS=('net_pnl','drawdown','closed_positions','winning_positions',
         'maximum_position_risk','maximum_stop_distance','terminal_valid',
         'capital_dollar_seconds','turnover')
LEDGERS=('episode_profits','episode_entry_clocks','episode_exit_clocks','episode_listings','open_episode_listings','open_episode_realized')


def compact(metrics):
    n=len(metrics['net_pnl']);result={}
    for key in SCALARS:
        values=np.asarray(metrics[key],dtype=bool if key=='terminal_valid' else np.float64)
        if values.shape!=(n,):raise ValueError('Controller scalar alignment changed')
        values.flags.writeable=False;result[key]=values
    for key in LEDGERS:
        if len(metrics[key])!=n:raise ValueError('Controller ledger alignment changed')
        rows=[]
        for row in metrics[key]:
            values=np.asarray(row,dtype=np.float64 if key in ('episode_profits','open_episode_realized') else np.int64)
            values.flags.writeable=False;rows.append(values)
        result[key]=rows
    if 'ticker_score' in metrics:result['ticker_score']=metrics['ticker_score']
    return result
