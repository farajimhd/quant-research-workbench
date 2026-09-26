import pytest

from src.market_engine.level_book_store import read, write


def test_immutable_write_accepts_same_json_value_after_restart(tmp_path):
    path = tmp_path / 'plan.json'
    write(path, {'feature_names': ('a', 'b'), 'version': 1})
    write(path, {'feature_names': ('a', 'b'), 'version': 1})
    assert read(path)['feature_names'] == ['a', 'b']
    with pytest.raises(ValueError, match='Immutable artifact differs'):
        write(path, {'feature_names': ('a', 'c'), 'version': 1})
