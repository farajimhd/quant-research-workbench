"""Upstream grants are separate, bounded and use shared restart-safe provisioning."""
import pytest

from scripts.clickhouse import provision_current_market_day_principals as operator
from scripts.clickhouse.provision_program_app_principals import apply_with_clients
from test_provision_program_app_principals import Admin, Client


def test_plan_only_does_not_open_operator_connection(monkeypatch, capsys):
    monkeypatch.setattr(operator, '_admin_client', lambda *args: pytest.fail('Opened admin in plan mode'))
    assert operator.main(['--source-year', '2026']) == 0
    assert 'PLAN ONLY' in capsys.readouterr().out
    reader, writer = operator.desired_plans((2026,))
    assert not reader.writes
    assert all(database == 'arte' for database, _ in writer.writes)
    assert ('market_sip_compact', 'events_2026') in reader.reads
    assert ('arte', 'market_day_build_fence_v1') in writer.writes
    assert len(writer.writes) == len(set(writer.writes))


@pytest.mark.parametrize('blocked', [False, True])
def test_storage_audit_precedes_all_secret_and_account_mutations(blocked):
    admin, clients, audited = Admin(), [], []
    plans = operator.desired_plans((2026,))
    def audit(client):
        assert client is admin and not admin.changes
        audited.append(True)
        if blocked:
            raise ValueError('Off-policy upstream output')
    def credentials(plan, **kwargs):
        assert audited
        return (plan.principal + '-private').ljust(64, 'x')
    def client_factory(user, password, *, readonly):
        client = Client(admin, user, readonly)
        clients.append(client)
        return client
    def run():
        apply_with_clients(admin=admin, plans=plans, credential_factory=credentials,
            client_factory=client_factory, storage_audit=audit, emit=lambda _: None)
    if blocked:
        with pytest.raises(ValueError, match='Off-policy'):
            run()
        assert not admin.changes and not clients
    else:
        run()
        assert set(admin.accounts) == {p.principal for p in plans}
        assert all(client.closed for client in clients)
        for plan in plans:
            assert admin.accounts[plan.principal] == {('SELECT', *s) for s in plan.reads} | {('INSERT', *s) for s in plan.writes}
