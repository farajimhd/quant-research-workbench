"""Writer-scope component tests with controlled issuer/source-fact seams."""
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_lot_publication_reuse as reuse
from src.trading_runtime import fixed_structural_lot_entry_v4 as entry
from src.trading_runtime.publication_source_reuse_policy import PublicationSourceReusePolicy


def fixture(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_source as source_module
    from src.backend import backtest_fixed_lot_management_reuse as management
    state = {'calls': 0, 'fact': 'causal-original', 'dependency': object()}
    source = SimpleNamespace(require_prepared_source=lambda: None,
        require_installed_admission=lambda: None)
    context = object.__new__(entry.FixedStructuralLotPublicationContext)
    for name, value in [('source', source), ('unit', {'packet': 'original'}), ('record', {'record': 'original'})]:
        object.__setattr__(context, name, value)
    monkeypatch.setattr(reuse, 'selected_publication_source_reuse_policy', lambda source: PublicationSourceReusePolicy(4096))
    monkeypatch.setattr(source_module, '_source_identity', lambda source: ('issued-source-fixture',))
    monkeypatch.setattr(management, '_policy_snapshot', lambda *args: (1, 2, 3, 4, 5, 6, ()))
    monkeypatch.setattr(management, '_entry_content', lambda request: str(request))
    monkeypatch.setattr(management, '_entry_source_facts', lambda request: state['fact'])
    monkeypatch.setattr(management, '_entry_dependencies', lambda request: (state['dependency'],))
    def original(self):
        state['calls'] += 1
        return {'verified': True}
    monkeypatch.setattr(entry.FixedStructuralLotPublicationContext, '_verify_source_complete', original)
    return context, state


def test_selected_scope_runs_original_once_and_clears(monkeypatch):
    context, state = fixture(monkeypatch)
    with reuse.publication_source_scope(context):
        values = [context.verify_source() for _ in range(4)]
        assert all(value is values[0] for value in values)
    assert state['calls'] == 1 and reuse._PUBLICATION.get() is None
    context.verify_source()
    assert state['calls'] == 2


@pytest.mark.parametrize('mutation', ['packet', 'source_fact', 'dependency', 'result'])
def test_selected_scope_rejects_changed_evidence_and_clears(monkeypatch, mutation):
    context, state = fixture(monkeypatch)
    with pytest.raises(ValueError):
        with reuse.publication_source_scope(context):
            request = context.verify_source()
            if mutation == 'packet': context.unit['packet'] = 'changed'
            elif mutation == 'source_fact': state['fact'] = 'changed'
            elif mutation == 'dependency': state['dependency'] = object()
            else: request['verified'] = False
            context.verify_source()
    assert state['calls'] == 1 and reuse._PUBLICATION.get() is None
