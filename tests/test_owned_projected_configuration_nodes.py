import pytest

from tests.test_exact_projected_configuration_nodes import common, texts
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.owned_scalar_row_snapshots import OwnedScalarRowSnapshots
from src.trading_runtime.owned_projected_configuration_nodes import OwnedProjectedConfigurationNodeCache
from src.trading_runtime.exact_projected_configuration_nodes import ExactProjectedConfigurationNodeCache


def test_complete_projection_is_identical_and_snapshot_owned_across_hits():
    bounds = dict(max_entries=2, max_input_bytes=100000, max_rows=100, max_bytes=1000000)
    owner = OwnedScalarRowSnapshots(max_entries=2, max_rows=100, max_bytes=1000000)
    old = ExactProjectedConfigurationNodeCache(**bounds)
    new = OwnedProjectedConfigurationNodeCache(ownership=owner, **bounds)
    arguments = canonical_json(common()), texts()
    expected = old.project(*arguments)
    actual = new.project(*arguments)
    assert actual == expected
    assert owner.snapshot(actual.rows) == owner.ordinary_snapshot(actual.rows)
    assert new.project(*arguments) is actual
    assert owner.statistics()['misses'] == 1
    with pytest.raises(TypeError):
        actual.rows[0]['run_id'] = 'foreign'


def test_ownership_eviction_preserves_old_complete_projection_fallback():
    bounds = dict(max_entries=2, max_input_bytes=100000, max_rows=100, max_bytes=1000000)
    owner = OwnedScalarRowSnapshots(max_entries=1, max_rows=100, max_bytes=1000000)
    selected = OwnedProjectedConfigurationNodeCache(ownership=owner, **bounds)
    first = selected.project(canonical_json(common()), texts(1))
    selected.project(canonical_json(common()), texts(2))
    assert owner.snapshot(first.rows) is None
    assert selected.project(canonical_json(common()), texts(1)) is first
    assert owner.ordinary_snapshot(first.rows) is not None


def test_oversized_projection_remains_complete_and_uncached():
    owner = OwnedScalarRowSnapshots(max_entries=2, max_rows=100, max_bytes=1)
    new = OwnedProjectedConfigurationNodeCache(ownership=owner, max_entries=2,
        max_input_bytes=1, max_rows=100, max_bytes=1000000)
    actual = new.project(canonical_json(common()), texts())
    expected = ExactProjectedConfigurationNodeCache(max_entries=2,
        max_input_bytes=100000, max_rows=100, max_bytes=1000000).project(canonical_json(common()), texts())
    assert actual == expected
    assert new.statistics()['entries'] == owner.statistics()['entries'] == 0
