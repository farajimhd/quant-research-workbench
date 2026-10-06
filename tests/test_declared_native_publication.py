"""Generic durable entry point cannot promote prepared packets to cash authority."""
import json
from dataclasses import replace

import pytest

from test_arte_declared_native_management_v4 import packet, case
from src.trading_runtime.arte_declared_native_v4_unit import DeclaredNativeV4Unit
from src.trading_runtime.arte_declared_native_publication import (
    DeclaredNativePublicationContext, publish_declared_native_v4,
    verify_declared_publication_graph,
)
from src.trading_runtime.arte_journal_commit_v4 import _publish_sealed_batch_v4, _load_verified_details_v4
from src.trading_runtime.arte_journal_writer import _sealed_families, _canonical_typed_content, _CONTRACTS


class NoIO:
    def __init__(self):
        self.calls = []
    def execute(self, sql):
        self.calls.append(sql)
        raise AssertionError('Unapproved native publication touched the durable client')


def context(c):
    return DeclaredNativePublicationContext(DeclaredNativeV4Unit(c.packet), **c.args, predecessor=c.predecessor)


@pytest.mark.parametrize('mutation', ['none', 'duplicate', 'foreign_run', 'list', 'proxy', 'bound'])
def test_recovery_context_index_is_exact_and_bounded(case, mutation):
    from src.trading_runtime.arte_declared_native_publication import declared_contexts_by_batch
    c = context(case)
    run_id, contexts, bound = c.unit.base.run_id, (c,), 10
    if mutation == 'duplicate': contexts = (c, c)
    elif mutation == 'foreign_run': run_id = 'foreign'
    elif mutation == 'list': contexts = [c]
    elif mutation == 'proxy': contexts = (object(),)
    elif mutation == 'bound': bound = 0
    if mutation == 'none':
        result = declared_contexts_by_batch(run_id, contexts, max_commits=bound)
        assert result == {c.unit.base.batch_id: c}
        assert result[c.unit.base.batch_id] is c
    else:
        with pytest.raises(ValueError):
            declared_contexts_by_batch(run_id, contexts, max_commits=bound)


def test_public_commit_reader_forwards_exact_native_context(case, monkeypatch):
    from test_arte_journal_commit_v4 import source, MemoryClient, prepare_commit_v4
    from src.trading_runtime import arte_journal_commit_v4 as subject
    commit, families = prepare_commit_v4(**source())
    client = MemoryClient()
    client.tables = {'trading_commit_v4': [dict(commit)],
                     'trading_commit_family_v4': [dict(row) for row in families]}
    c = context(case)
    observed = []
    def stop_before_details(*args, **kwargs):
        observed.append(kwargs['declared_native_context'])
        raise RuntimeError('test transport boundary')
    monkeypatch.setattr(subject, '_load_verified_details_v4', stop_before_details)
    with pytest.raises(RuntimeError, match='test transport boundary'):
        subject.load_verified_commit_v4(client, run_id=commit['run_id'],
            batch_id=commit['batch_id'], declared_native_context=c)
    assert observed == [c] and observed[0] is c


@pytest.mark.parametrize('mutation', ['none', 'missing', 'extra', 'hash', 'duplicate'])
def test_cold_graph_matches_source_unit_transport_only(case, monkeypatch, mutation):
    from src.trading_runtime.arte_declared_native_publication import (
        _declared_identities, verify_declared_cold_graph,
    )
    # Isolate graph integrity; this fixture grants no production admission.
    monkeypatch.setattr(DeclaredNativePublicationContext, 'verify_admission', lambda self: None)
    c = context(case)
    details = _declared_identities(c)
    name = next(iter(details))
    if mutation == 'missing': details.pop(name)
    elif mutation == 'extra': details['foreign'] = []
    elif mutation == 'hash': details[name][0] = (details[name][0][0], '0' * 64)
    elif mutation == 'duplicate': details[name].append(details[name][0])
    if mutation == 'none': verify_declared_cold_graph(details, c)
    else:
        with pytest.raises(ValueError, match='cold graph'):
            verify_declared_cold_graph(details, c)


@pytest.mark.parametrize('mutation', ['none', 'missing', 'duplicate', 'count', 'bool'])
def test_cold_inventory_matches_full_unit_transport_only(case, monkeypatch, mutation):
    from src.trading_runtime.arte_declared_native_publication import (
        _declared_identities, verify_declared_cold_inventory,
    )
    monkeypatch.setattr(DeclaredNativePublicationContext, 'verify_admission', lambda self: None)
    c = context(case)
    rows = [{'family_name': name, 'row_count': len(values)}
            for name, values in _declared_identities(c).items()]
    if mutation == 'missing': rows.pop()
    elif mutation == 'duplicate': rows.append(rows[0])
    elif mutation == 'count': rows[0]['row_count'] += 1
    elif mutation == 'bool': rows[0]['row_count'] = True
    if mutation == 'none':
        verify_declared_cold_inventory(c.unit.base.run_id, c.unit.base.batch_id, rows, c)
    else:
        with pytest.raises(ValueError, match='cold inventory'):
            verify_declared_cold_inventory(c.unit.base.run_id, c.unit.base.batch_id, rows, c)


def test_public_entry_path_fails_historical_admission_before_any_durable_io(case):
    client = NoIO()
    with pytest.raises(ValueError, match='historical'):
        publish_declared_native_v4(client, context(case))
    assert client.calls == []


def test_all_real_management_commands_fail_historical_admission_before_any_durable_io(packet):
    client = NoIO()
    c = DeclaredNativePublicationContext(DeclaredNativeV4Unit(packet.packet), **packet.args)
    with pytest.raises(ValueError, match='historical'):
        publish_declared_native_v4(client, c)
    assert client.calls == []


def test_generic_publication_rejects_own_events_without_source_context(case):
    c = context(case)
    base = _sealed_families(c.unit.base, declared_unit=c.unit)
    client = NoIO()
    # Even omitting EVERY own companion family cannot turn it into a base batch.
    with pytest.raises(ValueError, match='source context'):
        _publish_sealed_batch_v4(client, c.unit.base, base, base)
    assert client.calls == []


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'detached', 'proxy'])
def test_complete_publication_graph_cannot_be_changed(case, mutation):
    c = context(case)
    base = _sealed_families(c.unit.base, declared_unit=c.unit)
    families = (*base, *c.unit.packet.families)
    batch = c.unit.base
    if mutation == 'missing': families = families[:-1]
    elif mutation == 'duplicate': families = (*families, families[-1])
    elif mutation == 'detached': batch = replace(batch)
    else: c = object()
    with pytest.raises(ValueError, match='graph|source context'):
        verify_declared_publication_graph(batch, base, families, c)


def test_complete_graph_still_requires_independent_financial_admission(case):
    c = context(case)
    base = _sealed_families(c.unit.base, declared_unit=c.unit)
    with pytest.raises(ValueError, match='historical'):
        verify_declared_publication_graph(c.unit.base, base, (*base, *c.unit.packet.families), c)


def test_financial_fixture_alone_cannot_open_installed_source_publication(case, monkeypatch):
    from src.trading_runtime import arte_declared_native_command_v4 as entry
    # Named fixture only: production financial verification stays closed.
    monkeypatch.setattr(entry, 'verify_declared_entry_financial_admission', lambda *args, **kwargs: None)
    client = NoIO()
    with pytest.raises(RuntimeError, match='installed source approval'):
        publish_declared_native_v4(client, context(case))
    assert client.calls == []


def test_registered_schema_cannot_bypass_native_cold_source_hook(case):
    c = context(case)
    name, rows = c.unit.packet.families[0]
    assert name in _CONTRACTS
    client = NoIO()
    with pytest.raises(ValueError, match='source context'):
        _load_verified_details_v4(client, run_id=c.unit.base.run_id, batch_id=c.unit.base.batch_id,
            family_rows=({'family_name': name, 'row_count': len(rows)},), max_rows_per_family=65536)
    assert client.calls == []


@pytest.mark.parametrize('profile', ['v1', 'backtest_v2', 'backtest_v3', 'live_v4'])
def test_native_companions_cannot_route_through_other_writer_profiles(case, profile):
    from src.trading_runtime.arte_journal_writer import _insert
    name, rows = case.packet.families[0]
    client = NoIO()
    with pytest.raises(ValueError, match='Backtest V4 profile'):
        _insert(client, name, rows, 'unregistered-native-fixture', journal_profile=profile)
    assert client.calls == []


def test_cold_event_guard_detects_omission_of_every_native_companion(case):
    c = context(case)
    sealed = dict(_sealed_families(c.unit.base, declared_unit=c.unit))
    event = sealed['trading_event_v1'][0]
    wire = _canonical_typed_content('trading_event_v1', {k:v for k,v in event.items() if k != 'content_hash'})
    wire['content_hash'] = event['content_hash']
    class EventClient:
        def __init__(self): self.calls = []
        def execute(self, sql):
            self.calls.append(sql)
            assert 'FROM arte.trading_event_v1 ' in sql
            return json.dumps(wire)
    client = EventClient()
    with pytest.raises(ValueError, match='complete companion/source hook'):
        _load_verified_details_v4(client, run_id=c.unit.base.run_id, batch_id=c.unit.base.batch_id,
            family_rows=({'family_name':'trading_event_v1','row_count':1},), max_rows_per_family=65536)
    assert len(client.calls) == 1
