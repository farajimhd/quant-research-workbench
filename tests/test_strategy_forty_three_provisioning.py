import pytest

from scripts.clickhouse.provision_strategy_forty_three_facts import GRANTS, PRINCIPAL, grant_set, statements


def test_operator_plan_contains_only_three_ssd_tables_and_narrow_grants():
    plan = statements()
    assert len(plan) == 9
    assert all("live_market_ssd" in row for row in plan[:3])
    assert all(row.startswith("GRANT SELECT,INSERT ON arte.strategy_forty_three_") for row in plan[3:6])
    assert all(row.startswith("GRANT SELECT ON arte.strategy_forty_three_") and row.endswith("backtest_v3_reader") for row in plan[6:])
    assert not any("PASSWORD" in row or "DROP " in row or "REVOKE " in row for row in plan)


def test_effective_producer_grants_are_exact_and_reject_roles_or_broad_access():
    class Client:
        def __init__(self, text):
            self.text = text
        def execute(self, query):
            assert query == "SHOW GRANTS FINAL"
            return self.text
    lines = "\n".join(f"GRANT {privilege} ON {table} TO {PRINCIPAL}" for privilege, table in sorted(GRANTS))
    assert grant_set(Client(lines)) == GRANTS
    for extra in (f"GRANT SELECT ON arte.* TO {PRINCIPAL}", f"GRANT ALL ON *.* TO {PRINCIPAL}",
                  f"GRANT other_role TO {PRINCIPAL}"):
        with pytest.raises(RuntimeError):
            grant_set(Client(lines + "\n" + extra))
