"""Conservative peak qualification never invents intermediate Portfolio marks."""
from dataclasses import replace
import pytest
from src.backend.program_terminal_portfolio_rollforward import qualify_terminal_portfolio_final_sync
from src.backend.program_terminal_financial_rollforward import TerminalClosingAccountingResult
from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
from src.trading_runtime.portfolio import PortfolioAccountState
from src.trading_runtime.simulated_broker import SimulationConfig
from tests.test_program_terminal_oms_observations import cancelled_image


@pytest.mark.parametrize('failure',[None,'peak','baseline','accounts','incomplete','nan'])
def test_final_sync_requires_existing_peak_to_bound_every_broker_mark(failure):
    _,_,facts=cancelled_image()
    state=PortfolioAccountState(profile=None,peak_net_liquidation=100100.,realized_pnl_baseline=0.)
    initial=PortfolioRecovery(facts['scope'].run_id,{'DU1':7},{'DU1':state},{},{},{},{})
    image=dict(account_ids=['DU1'],cash={'DU1':100000.},performance_extrema=dict(complete=True,equity_peak=100.))
    result=TerminalClosingAccountingResult(image,0,1,1,(),facts['scope'],facts['broker_snapshot'])
    if failure=='peak':image['performance_extrema']['equity_peak']=100.01
    elif failure=='baseline':state.realized_pnl_baseline=None
    elif failure=='accounts':image['account_ids'].append('foreign')
    elif failure=='incomplete':image['performance_extrema']['complete']=False
    elif failure=='nan':image['performance_extrema']['equity_peak']=float('nan')
    if failure:
        with pytest.raises(ValueError):qualify_terminal_portfolio_final_sync(initial,broker_result=result,simulation_config=SimulationConfig(initial_cash=100000.))
    else:
        qualify_terminal_portfolio_final_sync(initial,broker_result=result,simulation_config=SimulationConfig(initial_cash=100000.))
        assert state.peak_net_liquidation==100100. and state.realized_pnl_baseline==0.
