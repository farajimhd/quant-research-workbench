"""Selected entry reuse with actual native binder; installation seams explicit."""
import gc
from dataclasses import replace
from weakref import ref

import pytest

from test_fixed_structural_lot_source import prepared
from src.backend import backtest_fixed_lot_management_reuse as reuse
from src.backend.backtest_fixed_structural_lot_source import FixedStructuralLotRequest, PreparedFixedStructuralLotSource
from src.trading_runtime.fixed_lot_management_reuse_policy import FixedLotManagementReusePolicy


def selected(monkeypatch):
    source, proposal, _ = prepared(monkeypatch)
    request = source.request(proposal)
    # Component-only installation seam: original complete entry binder remains real.
    monkeypatch.setattr(reuse, 'installed_management_reuse_policy', lambda _: FixedLotManagementReusePolicy())
    monkeypatch.setattr(PreparedFixedStructuralLotSource, 'require_installed_admission', lambda _: None)
    original = FixedStructuralLotRequest._verify_complete
    calls = []
    def verify(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(FixedStructuralLotRequest, '_verify_complete', verify)
    return source, request, calls


def test_actual_entry_complete_replay_once_and_cold_always_recomputes(monkeypatch):
    _, request, calls = selected(monkeypatch)
    reuse._verify_management_entry(request)
    reuse._verify_management_entry(request)
    assert len(calls) == 1
    request.verify()
    request.verify()
    assert len(calls) == 3


def test_reused_entry_rejects_mutable_carried_content(monkeypatch):
    _, request, _ = selected(monkeypatch)
    reuse._verify_management_entry(request)
    object.__setattr__(request, 'original', replace(request.original, reason='foreign'))
    with pytest.raises(ValueError, match='snapshot or dependency changed'):
        reuse._verify_management_entry(request)


def test_dead_request_evicted_without_retaining_source(monkeypatch):
    source, request, calls = selected(monkeypatch)
    reuse._verify_management_entry(request)
    identity = id(request)
    weak_request = ref(request)
    calls.clear()
    del request
    gc.collect()
    assert weak_request() is None
    assert identity not in reuse._ENTRIES[source]
    weak_source = ref(source)
    del source
    gc.collect()
    assert weak_source() is None


def test_prefix_retains_exact_immutable_tuple_reference():
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    prefix = V4CommittedPrefix('run', 3, 'batch', 'cursor', 'running', ('a', 'b'))
    binding = reuse._prefix_binding(prefix)
    assert binding[-1] is prefix.batch_ids
    object.__setattr__(prefix, 'batch_ids', tuple(['a', 'b']))
    assert binding[-1] is not prefix.batch_ids


@pytest.mark.parametrize('fact', ['price', 'momentum', 'selection', 'levels'])
def test_actual_nested_source_content_mutation_rejected(monkeypatch, fact):
    source, request, _ = selected(monkeypatch)
    reuse._verify_management_entry(request)
    plan = source.price_authority.plan
    if fact == 'price':
        columns = list(plan.source.observations)
        changed = columns[0].copy()
        changed[:] += 1
        columns[0] = changed
        object.__setattr__(plan.source, 'observations', tuple(columns))
    elif fact == 'momentum':
        changed = plan.momentum.current_line.copy()
        changed[:] += 1
        object.__setattr__(plan.momentum, 'current_line', changed)
    elif fact == 'selection':
        changed = plan.source.parent.eligible_mask.copy()
        changed[:] = False
        object.__setattr__(plan.source.parent, 'eligible_mask', changed)
    else:
        rows = source.intervals.intervals[0][1]
        object.__setattr__(rows[0], 'upper', rows[0].upper + 1)
    with pytest.raises(ValueError):
        reuse._verify_management_entry(request)


def test_context_request_reuse_counts_complete_binder_and_preserves_proposal_identity(monkeypatch):
    from types import SimpleNamespace
    source, request, _ = selected(monkeypatch)
    class Owner:
        operation = SimpleNamespace(source=source)
    owner = Owner()
    frontier = (source, object())
    # External writer/profile/lease boundary component seam only.
    monkeypatch.setattr(reuse, '_context_frontier', lambda actual: frontier if actual is owner else ())
    complete = PreparedFixedStructuralLotSource._request_complete
    calls = []
    def counted(actual, proposal):
        calls.append(proposal)
        return complete(actual, proposal)
    monkeypatch.setattr(PreparedFixedStructuralLotSource, '_request_complete', counted)
    proposal = request.entry.proposal
    token = reuse._CONTEXT_OWNER.set((owner, frontier))
    try:
        first = source.request(proposal)
        second_proposal = replace(proposal)
        second = source.request(second_proposal)
        assert first.entry.proposal is proposal
        assert second.entry.proposal is second_proposal
        assert first.intent == second.intent and first.entry.targets == second.entry.targets
        assert len(calls) == 1
    finally:
        reuse._CONTEXT_OWNER.reset(token)
    source.request(proposal)
    source.request(proposal)
    assert len(calls) == 3  # Ordinary/cold binders still recompute independently.


def test_context_request_rejects_changed_fact_and_dependency(monkeypatch):
    from types import SimpleNamespace
    source, request, _ = selected(monkeypatch)
    class Owner:
        operation = SimpleNamespace(source=source)
    owner = Owner()
    frontier = (source, object())
    monkeypatch.setattr(reuse, '_context_frontier', lambda _: frontier)
    token = reuse._CONTEXT_OWNER.set((owner, frontier))
    try:
        proposal = request.entry.proposal
        source.request(proposal)
        with pytest.raises(ValueError):
            source.request(replace(proposal, reference_ask=proposal.reference_ask + .01))
        method = PreparedFixedStructuralLotSource._request_complete
        monkeypatch.setattr(PreparedFixedStructuralLotSource, '_request_complete', lambda self, value: method(self, value))
        with pytest.raises(ValueError, match='dependencies changed'):
            source.request(proposal)
    finally:
        reuse._CONTEXT_OWNER.reset(token)


def test_unissued_management_owner_cannot_open_context_reuse():
    from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    owner = object.__new__(NativeFixedStructuralLotManagement)
    with pytest.raises(ValueError, match='issued owner'):
        reuse._context_frontier(owner)


def test_same_named_foreign_proposal_cannot_hit_warm_snapshot(monkeypatch):
    from dataclasses import fields, make_dataclass
    from types import SimpleNamespace
    source, request, _ = selected(monkeypatch)
    class Owner:
        operation = SimpleNamespace(source=source)
    owner = Owner()
    frontier = (source, object())
    monkeypatch.setattr(reuse, '_context_frontier', lambda _: frontier)
    proposal = request.entry.proposal
    fake_type = make_dataclass(type(proposal).__name__, [(f.name, object) for f in fields(proposal)], frozen=True)
    fake = fake_type(**{f.name: getattr(proposal, f.name) for f in fields(proposal)})
    assert reuse._content(fake) == reuse._content(proposal)
    token = reuse._CONTEXT_OWNER.set((owner, frontier))
    try:
        source.request(proposal)
        with pytest.raises(ValueError):
            source.request(fake)
    finally:
        reuse._CONTEXT_OWNER.reset(token)


def test_forged_owner_cannot_issue_constructor_binding():
    from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    owner = object.__new__(NativeFixedStructuralLotManagement)
    with pytest.raises(ValueError, match='constructed management owner'):
        reuse.issue_management_owner(owner)


def test_snapshot_encoder_bytes_match_original_and_rejections():
    from dataclasses import dataclass
    from collections import OrderedDict
    from datetime import date,datetime,timezone
    from decimal import Decimal
    from enum import IntEnum,StrEnum
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    from src.trading_runtime.journal_contract import canonical_json
    @dataclass
    class Value:
        payload:object
    class N(IntEnum): ONE=1
    class S(StrEnum): ONE='one'
    class Integer(int): pass
    class String(str): pass
    class Date(date): pass
    cases=(None,True,1,-1,0.,-0.,'text',N.ONE,S.ONE,
        date(2026,1,1),datetime(2026,1,1,tzinfo=timezone.utc),Decimal('1.00'),
        OrderedDict((('z',[1,False]),('a',Value((Decimal('2.0'),N.ONE))))),
        Value({'tuple':(1,2),'set':frozenset((3,1))}),
        Integer(1),String('x'),Date(2026,1,1),object(),{1:'a','x':'b'})
    for value in cases:
        try: expected=canonical_json(_small_tree(value))
        except Exception as error:
            with pytest.raises(type(error)) as actual:reuse._content(value)
            assert str(actual.value)==str(error)
        else:assert reuse._content(value)==expected
