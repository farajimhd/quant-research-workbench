"""The shared volume product adds exactly two tables and six explicit grants."""
from scripts.clickhouse.plan_program_app_principals import desired_plans
from src.backend.qmd_volume_reader import permission_preflight
from src.market_engine.qmd_volume_product import TABLES, plan_required_grants
from tests.test_program_app_principal_plan import GrantClient


def test_new_volume_scopes_add_only_exact_reader_and_publisher_grants():
    profiles = desired_plans()
    actual = {grant for profile in profiles for grant in profile.grants()
              if any(f'ON q_live.{table.name} ' in grant for table in TABLES)}
    assert actual == set(plan_required_grants())
    assert len(actual) == 6
    assert {p.principal for p in profiles if any(('q_live', t.name) in p.reads for t in TABLES)} == {
        'program_market_input_reader', 'program_history_publisher'}


def test_actual_shared_principal_plans_pass_volume_native_permission_gates():
    profiles = {p.principal: p for p in desired_plans()}
    assert permission_preflight(GrantClient(profiles['program_market_input_reader'])) == 'program_market_input_reader'
    assert permission_preflight(GrantClient(profiles['program_history_publisher']), writable=True) == 'program_history_publisher'
