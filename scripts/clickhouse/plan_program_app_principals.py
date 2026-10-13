"""Review-only credential scopes for current programme input/publication paths.

No connections, credentials, accounts, grants, rows, or services are opened.
Publication and producer execution require separately approved operator steps.
"""
import os
import argparse
from pathlib import Path
import sys
from dataclasses import dataclass

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.clickhouse.provision_fixed_backtest_v3_principals import desired_plan as existing_read_plan
from src.market_engine.columnar_history_product import TABLES as HISTORY
from src.market_engine.opening_price_reference import TABLES as OPENING
from pipelines.market_sip.events.liquidity_resolution_sql import TABLES as LIQUIDITY
from src.trading_runtime.columnar_strategy_release import TABLES as RELEASE
from src.trading_runtime.strategy_configuration_schema import TABLES as SELECTION

SYSTEM = frozenset(('system', name) for name in
    ('tables', 'columns', 'parts', 'storage_policies', 'data_skipping_indices'))


def scopes(tables):
    return frozenset((table.database, table.name) for table in tables)


@dataclass(frozen=True)
class ProgramPrincipalPlan:
    principal: str
    reads: frozenset
    writes: frozenset = frozenset()

    def grants(self):
        return tuple(f'GRANT {privilege} ON {database}.{table} TO {self.principal}'
            for privilege, targets in (('SELECT', self.reads), ('INSERT', self.writes))
            for database, table in sorted(targets))


def desired_plans():
    """Separate exact release/selection readers from producer source readers.

    Source readers reuse the approved app's existing reference/market read
    catalogue; their source lineage checks remain mandatory. No existing
    account receives new grants. All writes target central declared contracts.
    """
    base = existing_read_plan()[0]
    inherited = ({('arte', name) for name in base.select_arte}
        | set(base.select_reference) | {('system', name) for name in base.select_system})
    history, opening, liquidity = scopes(HISTORY), scopes(OPENING), scopes(LIQUIDITY)
    release, selection = scopes(RELEASE), scopes(SELECTION)
    opening_parents = frozenset((('arte', 'bars_v1'),
        ('q_live', 'id_symbol_interval_v1'), ('q_live', 'market_stock_split_v1')))
    liquidity_parents = frozenset((('arte', 'liquidity_100ms_v1'),
        ('arte', 'liquidity_execution_price_100ms_v1')))
    return (
        ProgramPrincipalPlan('program_market_input_reader',
            frozenset(inherited) | SYSTEM | history | opening | liquidity | opening_parents | liquidity_parents),
        ProgramPrincipalPlan('program_history_publisher', SYSTEM | history, history),
        ProgramPrincipalPlan('program_opening_reference_publisher', SYSTEM | opening | opening_parents, opening),
        ProgramPrincipalPlan('program_liquidity_publisher', SYSTEM | liquidity | liquidity_parents, liquidity),
        ProgramPrincipalPlan('program_release_reader', SYSTEM | release),
        ProgramPrincipalPlan('program_release_publisher', SYSTEM | release, release),
        ProgramPrincipalPlan('program_selection_reader', SYSTEM | selection),
        ProgramPrincipalPlan('program_selection_publisher', SYSTEM | selection, selection),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-grants', action='store_true',
        help='Print the complete explicit table grants for review')
    args = parser.parse_args(argv)
    print('PLAN ONLY | 8 isolated principals | No connection or operational change')
    for plan in desired_plans():
        print(f'{plan.principal}: SELECT {len(plan.reads)}, INSERT {len(plan.writes)}')
        if args.show_grants:
            for statement in plan.grants():
                print(statement)
    print('Not authorized here: account creation, producers, publication, services, backtests')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
