"""Reviewed optional waiting geometry remains sealed through legacy native routes."""
from pathlib import Path
import pytest
from src.backend import backtest_fixed_v4_certification as cert

ROOT = Path(__file__).parents[1]
NUMBERS = (1, 42, 47, 48, 50, 52, 57, 58, 59, 60, 61, 64)
HELPER = 'src/trading_runtime/squeeze_ladder_geometry.py'
SHARED_MUTATIONS = (
    (HELPER, '0 <= self.trigger_source_row_index < 2**32', '0 <= self.trigger_source_row_index'),
    ('src/backend/backtest_fixed_journal_bootstrap.py',
     "expected_principal = 'backtest_v4_waiting_ladder_runner'", "expected_principal = 'backtest_v4_ladder_runner'"),
    ('src/trading_runtime/arte_journal_writer.py',
     "_validate_ladder_geometry_profile(getattr(client, 'automatic_ladder_profile', False), geometry_policy)", 'None'),
    ('src/backend/replay_run_service.py',
     "\n        reader = backtest_v4_operator_client_from_env(**declared_ladder_runner_options(self.definition.configuration_revision['payload']))",
     '\n        reader = backtest_v4_operator_client_from_env(automatic_ladder=True)'),
    ('src/backend/backtest_declared_ladder_plan.py',
     'return declared_ladder_policy(SimpleNamespace(payload=configuration))', 'return None'),
    ('src/backend/backtest_ladder_source_authority.py',
     'if declared is None:', 'if True:'),
    ('src/trading_runtime/squeeze_ladder_automatic.py',
     'lot_count=3, allocation=', 'lot_count=4, allocation='),
)


@pytest.mark.parametrize('number', NUMBERS)
@pytest.mark.parametrize('relative, before, after', SHARED_MUTATIONS)
def test_actual_native_route_rejects_changed_waiting_dependency(monkeypatch, number, relative, before, after):
    # Warm the real bounded summary cache, never a cached approval.
    assert len(cert.certify_numbered_fixed_v4_projection(number)) == 64
    original = Path.read_text
    target = (ROOT / relative).resolve()
    text = target.read_text(encoding='utf-8')
    assert text.count(before) == 1
    changed = text.replace(before, after, 1)
    assert changed != text and changed.count(before) == 0
    reads = []
    def changed_source(path, *args, **kwargs):
        if path.resolve() == target:
            reads.append(True)
            return changed
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', changed_source)
    expected = ('Strategy 13 reviewed source authority changed: ' + relative.removeprefix('src/')
                if relative == HELPER and number != 1 else 'Drawdown core source changed: ' + relative)
    with pytest.raises(ValueError, match=expected):
        cert.certify_numbered_fixed_v4_projection(number)
    assert reads


def test_waiting_helper_exact_shared_keyset_and_unknown_override(tmp_path, monkeypatch):
    assert cert._DRAWDOWN_CORE_REQUIRED_SOURCE_FILES[-4] == HELPER
    assert tuple(cert._DRAWDOWN_CORE_REVIEWED_AST)[-4] == HELPER
    assert len(cert.certify_drawdown_measure_core_source()) == 64
    with pytest.raises(ValueError, match='override is unknown'):
        cert.certify_drawdown_measure_core_source(source_overrides={'foreign_geometry.py': tmp_path / 'never-read'})
    monkeypatch.setattr(cert, '_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES', cert._DRAWDOWN_CORE_REQUIRED_SOURCE_FILES[:-1])
    with pytest.raises(ValueError, match='keyset changed'):
        cert.certify_drawdown_measure_core_source()


@pytest.mark.parametrize('relative', (
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
))
@pytest.mark.parametrize('mutation', ('missing', 'extra'))
def test_classifier_symbol_metadata_is_closed(monkeypatch, relative, mutation):
    mapping = dict(cert._DRAWDOWN_CORE_REVIEWED_AST)
    symbols = dict(mapping[relative])
    if mutation == 'missing':
        symbols.pop(next(iter(symbols)))
    else:
        symbols['foreign_symbol'] = '0' * 64
    mapping[relative] = symbols
    monkeypatch.setattr(cert, '_DRAWDOWN_CORE_REVIEWED_AST', mapping)
    with pytest.raises(ValueError, match='metadata shape changed'):
        cert.certify_drawdown_measure_core_source()


def test_unknown_dictionary_leaf_cannot_broaden_classifier_closure(monkeypatch):
    mapping = dict(cert._DRAWDOWN_CORE_REVIEWED_AST)
    mapping['foreign_classifier.py'] = {'foreign': '0' * 64}
    monkeypatch.setattr(cert, '_DRAWDOWN_CORE_REVIEWED_AST', mapping)
    monkeypatch.setattr(cert, '_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES',
                        cert._DRAWDOWN_CORE_REQUIRED_SOURCE_FILES + ('foreign_classifier.py',))
    with pytest.raises(ValueError, match='metadata shape changed'):
        cert.certify_drawdown_measure_core_source()
