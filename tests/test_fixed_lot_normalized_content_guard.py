"""Normalized native unit guard; ownership/frontier transport seams are explicit."""
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_lot_management_reuse as reuse
from src.backend import backtest_management_structural_guard as structural
from test_fixed_structural_lot_entry_v4 import packet


class Owner:
    pass


@pytest.fixture
def normalized_owner(monkeypatch):
    source, request, journal, record, unit = packet(monkeypatch)
    verified = []

    def original_source():
        request.verify()
        verified.append(request)
        return request

    context = SimpleNamespace(source=source, unit=unit, record=record,
                              verify_source=original_source)
    owner = Owner()
    owner.operation = SimpleNamespace(source=source)
    owner.client = SimpleNamespace(fixed_structural_lot_contexts=(context,),
                                   fixed_lot_recovery_contexts=())
    # This test owns normalized-content verification, not a writer lease or
    # indexed market-source certification. The original native source callback
    # and the actual semantic batch and journal record remain real.
    monkeypatch.setattr(reuse, '_context_frontier', lambda actual: (actual,))
    monkeypatch.setattr(reuse, '_entry_source_facts', lambda actual: 'indexed facts seam')
    monkeypatch.setattr(reuse, '_entry_dependencies', lambda actual: ())
    try:
        yield owner, context, verified
    finally:
        journal.close()


def test_full_initial_source_then_fresh_descendants_without_json_rebuilding(normalized_owner, monkeypatch):
    owner, context, verified = normalized_owner
    original = reuse._content

    def scalar_content(value):
        if (type(value) is tuple and len(value) == 2
                and value[0] is context.unit and value[1] is context.record):
            pytest.fail('Normalized native unit was serialized again')
        return original(value)

    monkeypatch.setattr(reuse, '_content', scalar_content)
    before = reuse._normalized_context_snapshot(owner)
    assert len(verified) == 1
    guard = before[4][0][2]
    assert type(guard) is structural.ManagementStructuralGuard
    for _ in range(3):
        after = reuse._normalized_context_snapshot(owner, before[4])
        assert reuse._same_normalized_snapshot(before, after)
        assert after[4][0][2] is guard
    assert len(verified) == 1
    previous = context.record.payload
    object.__setattr__(context.record, 'payload', {**previous, 'unexpected': 1})
    try:
        with pytest.raises(ValueError, match='structural content changed'):
            reuse._normalized_context_snapshot(owner, before[4])
    finally:
        object.__setattr__(context.record, 'payload', previous)
    assert reuse._same_normalized_snapshot(before, reuse._normalized_context_snapshot(owner, before[4]))


@pytest.mark.parametrize('field', ('require_management_structural_guard', 'capture_management_structural_guard'))
def test_guard_function_code_change_invalidates_read_proof(normalized_owner, monkeypatch, field):
    owner, _, _ = normalized_owner
    before = reuse._normalized_context_snapshot(owner)
    original = getattr(structural, field)
    def altered(guard, value=None):
        return guard
    monkeypatch.setattr(original, '__code__', altered.__code__)
    after = reuse._normalized_context_snapshot(owner, before[4])
    assert not reuse._same_normalized_snapshot(before, after)


def test_guard_dependency_substitution_invalidates_read_proof(normalized_owner, monkeypatch):
    owner, _, _ = normalized_owner
    before = reuse._normalized_context_snapshot(owner)
    original = structural.is_dataclass
    monkeypatch.setattr(structural, 'is_dataclass', lambda value: original(value))
    after = reuse._normalized_context_snapshot(owner, before[4])
    assert not reuse._same_normalized_snapshot(before, after)


@pytest.mark.parametrize('binding', ([], ((),), ((None, None),)))
def test_malformed_normalized_guard_binding_fails_closed(normalized_owner, binding):
    owner, _, _ = normalized_owner
    with pytest.raises(ValueError, match='normalized entry bindings changed'):
        reuse._normalized_context_snapshot(owner, binding)


def test_missing_issued_guard_cannot_recapture_changed_content(normalized_owner):
    owner, _, _ = normalized_owner
    before = reuse._normalized_context_snapshot(owner)
    context, request, _ = before[4][0]
    with pytest.raises(ValueError, match='Unissued management structural guard'):
        reuse._normalized_context_snapshot(owner, ((context, request, None),))
