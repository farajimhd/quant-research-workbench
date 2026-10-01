"""Additional source seal mutations; inherited full certification stays mandatory."""
from pathlib import Path

import pytest

from src.backend.backtest_strategy_confirmed_ah_certification import (
    CONFIRMED_AH_SOURCE_AST, certify_confirmed_ah_source,
)


def test_additional_source_graph_has_deterministic_exact_seal():
    first = certify_confirmed_ah_source()
    assert len(first) == 64 and certify_confirmed_ah_source() == first


@pytest.mark.parametrize('relative', list(CONFIRMED_AH_SOURCE_AST))
def test_each_changed_additional_source_is_rejected(tmp_path, relative):
    source = Path(relative).read_text(encoding='utf-8')
    changed = tmp_path / Path(relative).name
    changed.write_text(source + '\nforeign_authority = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned AH source changed'):
        certify_confirmed_ah_source(source_overrides={relative: changed})


def test_unknown_override_is_rejected():
    with pytest.raises(ValueError, match='outside its pinned authority'):
        certify_confirmed_ah_source(source_overrides={'foreign.py': Path('foreign.py')})


def test_number34_cannot_bypass_failed_complete_parent_proof(monkeypatch):
    from src.backend import backtest_fixed_v4_certification as certification
    actual = certification.certify_numbered_fixed_v4_projection
    calls = []
    def rejected_parent(number):
        calls.append(number)
        raise ValueError('parent source remains uncertified')
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', rejected_parent)
    with pytest.raises(ValueError, match='parent source remains uncertified'):
        actual(34)
    assert calls == [33]
