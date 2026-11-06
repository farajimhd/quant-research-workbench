"""Sealed split performance for display only; never a selection input."""
from pathlib import Path
from rich.table import Table
from .progress_sessions import read_verified


def load(root, generation):
    folder=Path(root)/f'generation-{generation:04d}'
    seal=read_verified(folder/'complete.json');result={};identity=None;population=None
    for role in ('training','validation'):
        digest=seal.get(role+'_sha256')
        if not digest:continue
        data=read_verified(folder/(role+'.json'),digest)
        ids=data['ids'];panel=data['panel']
        if len(set(ids))!=len(ids):raise ValueError('Duplicate performance IDs')
        if identity is not None and ids!=identity:raise ValueError('Performance split population mismatch')
        identity=ids
        # The seal's population_sha256 hashes the serialized file, while these
        # split fields identify the canonical genome population token.
        if population is not None and data['population_sha256']!=population:raise ValueError('Performance population token mismatch')
        population=data['population_sha256']
        coverage=len(data['receipts'])
        for i,serial in enumerate(ids):
            values={key:panel[key][i] for key in ('score','net_pnl','gross_losses','drawdown','closed_positions','winning_positions','profitable_sessions','net_excluding_best_session') if key in panel}
            trades=values.get('closed_positions');wins=values.get('winning_positions');profitable=values.get('profitable_sessions')
            values.update(coverage=coverage,win_rate=100*wins/trades if trades and wins is not None else None,
                profitable_percent=100*profitable/coverage if coverage and profitable is not None else None)
            result.setdefault(int(serial),{})[role]=values
    return result


def table(rows, performance, selection, start, totals):
    out=Table(expand=True,padding=(0,0))
    for label in ('Serial','Split','Score $','Net $','Loss $','DD sum $','Trades','Win %','Profit sessions','Profit %','Net ex best $'):
        out.add_column(label,justify='right',no_wrap=True)
    def number(value):return '-' if value is None else f'{value:,.2f}'
    for index,row in enumerate(rows,start):
        for role in ('training','validation'):
            values=performance.get(int(row['serial']),{}).get(role,{})
            covered=values.get('coverage');profitable=values.get('profitable_sessions')
            days='-' if covered is None or profitable is None else f'{int(profitable)}/{covered} of {totals[role]}'
            out.add_row(str(row['serial']), 'Train' if role=='training' else 'Val',
                *(number(values.get(key)) for key in ('score','net_pnl','gross_losses','drawdown','closed_positions','win_rate')),
                days,number(values.get('profitable_percent')),number(values.get('net_excluding_best_session')),
                style='bold bright_yellow on blue' if index==selection else None)
    return out
