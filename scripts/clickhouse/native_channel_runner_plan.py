"""Pure dedicated native-channel grant plan; no credentials or apply authority."""
from dataclasses import replace
from hashlib import sha256

from src.trading_runtime.native_channel_management_policy import selected_writer_contracts
from src.trading_runtime.native_channel_writer_profile import channel_runner_identity
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE


def desired_plan(channel_policy, risk_policy):
    """Retain inherited financial grants and add exact selected companions.

    Feature products are SELECT-only. This function does not install tables,
    provision credentials, change grants, or prove physical SSD placement.
    """
    _, principal = channel_runner_identity(channel_policy, risk_policy)
    if getattr(risk_policy, 'entry_cost_capability', False):
        from scripts.clickhouse.provision_backtest_v4_original_risk_entry_cost_runner import desired_plan as parent_plan
    else:
        from scripts.clickhouse.provision_backtest_v4_original_risk_runner import desired_plan as parent_plan
    parent = parent_plan(risk_policy)
    companions = frozenset(table.name for table in selected_writer_contracts(channel_policy))
    products = frozenset(table.removeprefix('arte.') for table in (FEATURE_TABLE, COVERAGE_TABLE))
    return replace(parent, principal=principal,
        select_arte=parent.select_arte | companions | products,
        insert_arte=parent.insert_arte | companions)


def apply_with_clients(*, admin, credential, client_factory, channel_policy, risk_policy):
    """Provision only after exact existing SSD schemas and parts are verified.

    No table creation or migration is performed. Private credentials are
    supplied by the operator-owned callback and never returned or logged.
    """
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import _desired_grants, _effective_grants
    from src.trading_runtime.arte_journal_schema import storage_preflight
    from src.backend.backtest_native_channel_store import storage_preflight as feature_storage_preflight
    from src.trading_runtime.arte_journal_writer import v4_storage_contracts, _v4_preflight
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import DIAGNOSTIC
    from src.trading_runtime.original_risk_pending_snapshot import selected_snapshot_contracts
    from src.trading_runtime.arte_entry_spread_risk_v4 import ENTRY_SPREAD_RISK
    plan=desired_plan(channel_policy,risk_policy)
    if admin.execute('SELECT currentUser()').strip()==plan.principal:
        raise ValueError('Native runner provisioning requires a distinct operator')
    present=admin.execute(f"SELECT count() FROM system.users WHERE name='{plan.principal}' FORMAT TabSeparated").strip()
    if present not in {'0','1'}:
        raise ValueError('Native runner principal inventory is ambiguous')
    contracts=(*v4_storage_contracts(),DIAGNOSTIC,*selected_snapshot_contracts(),
        *selected_writer_contracts(channel_policy))
    if getattr(risk_policy,'entry_cost_capability',False):
        contracts=(*contracts,ENTRY_SPREAD_RISK)
    storage_preflight(admin,tables=contracts)
    feature_storage_preflight(admin)
    password=credential(account_exists=present=='1')
    if type(password) is not str or len(password)<40:
        raise ValueError('Native runner requires its complete private credential')
    if present=='0':
        digest=sha256(password.encode()).hexdigest()
        admin.execute(f"CREATE USER {plan.principal} IDENTIFIED WITH sha256_hash BY '{digest}' "
            "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    runner=client_factory(plan.principal,password)
    try:
        if runner.execute('SELECT currentUser()').strip()!=plan.principal:
            raise ValueError('Native runner credential authenticates as another principal')
        have=_effective_grants(runner,plan)
        for privilege,database,table in sorted(_desired_grants(plan)-have):
            admin.execute(f'GRANT {privilege} ON {database}.{table} TO {plan.principal}')
        if _effective_grants(runner,plan)!=_desired_grants(plan):
            raise ValueError('Native runner effective grants differ from the exact plan')
        runner.automatic_ladder_profile=False
        runner.entry_spread_risk_profile=bool(getattr(risk_policy,'entry_cost_capability',False))
        runner.confirmed_original_risk_policy=risk_policy
        runner.native_channel_management_policy=channel_policy
        _v4_preflight(runner)
        feature_storage_preflight(runner)
    finally:
        runner.close()
    return plan
