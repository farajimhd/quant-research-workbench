from datetime import date
import json

import pytest

from research.rl_trading.v6.identity_map import (certify_identity_map,
                                                 open_identity_map)
from research.rl_trading.v6.session_data import PackedSession


def test_identity_sidecar_is_exact_and_hash_bound(tmp_path):
    bank_root = tmp_path / 'bank-day'
    bank_root.mkdir()
    (bank_root / 'plan.json').write_text(json.dumps({
        'population_snapshot_hash': 'pinned-population'}))
    session = PackedSession(date(2026, 7, 31), 'train', bank_root,
                            'bank-certificate', None, None, ('A', 'B'))
    population = [{'listing_id': 'B', 'ticker': 'BBB'},
                  {'listing_id': 'A', 'ticker': 'AAA'}]
    output = tmp_path / 'identity'
    cert = certify_identity_map(session, population, 'pinned-population',
                                output, runtime_root=tmp_path)
    assert cert['listings'] == 2
    assert open_identity_map(output, session,
                             runtime_root=tmp_path) == ('AAA', 'BBB')
    assert certify_identity_map(session, population, 'pinned-population',
                                output, runtime_root=tmp_path) == cert
    with pytest.raises(ValueError, match='differs'):
        certify_identity_map(session, population, 'wrong', output,
                             runtime_root=tmp_path)
