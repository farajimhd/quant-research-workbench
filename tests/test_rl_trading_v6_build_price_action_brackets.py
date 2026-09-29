from hashlib import sha256
import json

import polars as pl
import pytest

from research.rl_trading.v6.build_price_action_brackets import compile_brackets
from research.rl_trading.v6.build_price_action_geometry import VERSION as GEOMETRY_VERSION


def test_brackets_use_canonical_bar_precision_without_quote_fields(tmp_path):
    root = tmp_path / 'geometry'
    root.mkdir()
    geometry = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A:1'],
        'entry_price': [10.], 'swing_low_3s': [9.], 'held_min_low': [9.1],
        'held_max_high': [11.], 'held_last_max_high_us': [8_000_000],
        'expected_held_bars': [4], 'held_bars': [3],
        'clock_complete': [False]})
    geometry.write_parquet(root / 'geometry.parquet')
    checksum = sha256((root / 'geometry.parquet').read_bytes()).hexdigest()
    certificate = {'version': GEOMETRY_VERSION,
        'status': 'geometry_only_not_supervision',
        'execution_evidence': 'none',
        'label_evidence': 'certified_1s_candles_only',
        'geometry_sha256': checksum, 'episodes': 1, 'day': '2026-08-04'}
    (root / 'complete.json').write_text(json.dumps(certificate))
    result, _ = compile_brackets(root)
    assert result['oracle_stop'][0] == pytest.approx(8.9999)
    assert result['oracle_target'][0] == 11.
    assert result['label_available'][0]
    certificate['execution_evidence'] = 'quotes'
    (root / 'complete.json').write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match='quote-derived'):
        compile_brackets(root)
