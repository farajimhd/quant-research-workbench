"""Independent mutations of the complete Strategy64 source closure."""
from pathlib import Path

import pytest

from src.backend import backtest_strategy_sixty_four_certification as certification
from src.backend import backtest_strategy_confirmed_ah_certification as ah_certification
from src.backend import backtest_strategy_episode_activity_certification as episode_certification


@pytest.mark.parametrize('relative', certification.REQUIRED_SOURCE_FILES)
def test_each_reviewed_leaf_rejects_an_executable_ast_change(tmp_path, relative):
    original = Path(certification.__file__).parents[2] / relative
    changed = tmp_path / 'changed.py'
    changed.write_text(original.read_text(encoding='utf-8') + '\nsource_authority_mutation = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed') as caught:
        certification.certify_strategy_sixty_four_source(source_overrides={relative: changed})
    assert str(caught.value).endswith(relative)


def test_unknown_override_rejected_before_read(tmp_path):
    with pytest.raises(ValueError, match='outside reviewed authority'):
        certification.certify_strategy_sixty_four_source(source_overrides={'unreviewed.py': tmp_path / 'absent'})


@pytest.mark.parametrize('change', ['missing', 'extra'])
def test_incomplete_or_expanded_review_keyset_rejected(monkeypatch, change):
    changed = dict(certification.STRATEGY64_SOURCE_AST)
    if change == 'missing':
        changed.pop(next(iter(changed)))
    else:
        changed['unreviewed.py'] = '0' * 64
    monkeypatch.setattr(certification, 'STRATEGY64_SOURCE_AST', changed)
    with pytest.raises(ValueError, match='not sealed'):
        certification.certify_strategy_sixty_four_source()


def test_same_path_is_reread_after_successful_certification(tmp_path):
    relative = certification.REQUIRED_SOURCE_FILES[0]
    original = Path(certification.__file__).parents[2] / relative
    changed = tmp_path / 'source.py'
    changed.write_text(original.read_text(encoding='utf-8'), encoding='utf-8')
    digest = certification.certify_strategy_sixty_four_source(source_overrides={relative: changed})
    assert len(digest) == 64
    changed.write_text(changed.read_text(encoding='utf-8') + '\nsource_authority_mutation = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed'):
        certification.certify_strategy_sixty_four_source(source_overrides={relative: changed})


@pytest.mark.parametrize('module,certify,relative', [
    (module, certify, relative)
    for module, certify, mapping in (
        (ah_certification, ah_certification.certify_confirmed_ah_source, ah_certification.CONFIRMED_AH_SOURCE_AST),
        (episode_certification, episode_certification.certify_episode_activity_source, episode_certification.EPISODE_ACTIVITY_SOURCE_AST),
    )
    for relative in mapping
])
def test_transitive_source_leaf_mutations_reject(tmp_path, module, certify, relative):
    original = Path(module.__file__).parents[2] / relative
    changed = tmp_path / 'changed.py'
    changed.write_text(original.read_text(encoding='utf-8') + '\nsource_authority_mutation = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned .* source changed') as caught:
        certify(source_overrides={relative: changed})
    assert str(caught.value).endswith(relative)
