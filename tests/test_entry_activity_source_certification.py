"""Strategy36 composes complete parent proof and pins every extra authority."""
from pathlib import Path

import pytest

from src.backend.backtest_strategy_entry_activity_certification import (
    ENTRY_ACTIVITY_SOURCE_AST, certify_entry_activity_source,
)


def test_installed_compiler_and_full_parent_projection_are_certified():
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from pipelines.strategy_one.strategy_thirty_six_configuration import compile_strategy_thirty_six_configuration
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    from test_strategy_thirty_six_release import source_fixture, APPROVAL
    result = compile_strategy_thirty_six_configuration(source_fixture(), **APPROVAL)
    assert is_numbered_fixed_configuration(result['payload'])
    proof = certify_numbered_fixed_v4_projection(36)
    assert len(proof) == 64 and proof != certify_numbered_fixed_v4_projection(35)
    assert len(ENTRY_ACTIVITY_SOURCE_AST) == 32
    assert {'src/trading_runtime/arte_journal_rowbinary.py',
            'src/trading_runtime/arte_typed_insert_dispatch.py',
            'research/mlops/clickhouse.py'}.issubset(ENTRY_ACTIVITY_SOURCE_AST)


@pytest.mark.parametrize('relative', tuple(ENTRY_ACTIVITY_SOURCE_AST))
def test_each_extra_authority_rejects_changed_source(relative, tmp_path):
    source = (Path(__file__).parents[1] / relative).read_text(encoding='utf-8')
    changed = tmp_path / 'changed.py'
    changed.write_text(source + '\nunreviewed_activity_authority = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='source changed'):
        certify_entry_activity_source(source_overrides={relative: changed})


def test_extra_source_override_cannot_replace_parent_proof(tmp_path):
    with pytest.raises(ValueError, match='outside reviewed authority'):
        certify_entry_activity_source(source_overrides={'unknown.py': tmp_path / 'missing.py'})
