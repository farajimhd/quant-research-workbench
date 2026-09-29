import json

import pytest

from research.rl_trading.v6 import build_price_action_geometry_campaign as campaign


def test_forward_geometry_campaign_waits_for_certified_days(tmp_path, monkeypatch):
    first, second = campaign.DATES[:2]
    roots = {}
    for day in (first, second):
        root = tmp_path / str(day)
        root.mkdir()
        (root / 'complete.json').write_text('{}')
        roots[str(day)] = str(root)
    manifest = tmp_path / 'day-roots.json'
    manifest.write_text(json.dumps({'version':
        'rl-trading-v6-forward-candle-day-roots', 'day_roots': roots}))
    calls = []
    monkeypatch.setattr(campaign, 'build_day', lambda args: calls.append(args))
    state = campaign.run_available(manifest, tmp_path / 'geometry',
        early=tmp_path / 'early', late=tmp_path / 'late',
        ledger=tmp_path / 'ledger')
    assert state['completed'] == [str(first), str(second)]
    assert state['queued'][0] == str(campaign.DATES[2])
    assert len(calls) == 2
    # The polling process does not reopen completed days every minute.
    campaign.run_available(manifest, tmp_path / 'geometry',
        early=tmp_path / 'early', late=tmp_path / 'late',
        ledger=tmp_path / 'ledger', done=tuple(state['completed']))
    assert len(calls) == 2
    with pytest.raises(ValueError, match='prefix'):
        campaign.run_available(manifest, tmp_path / 'geometry',
            early=tmp_path / 'early', late=tmp_path / 'late',
            ledger=tmp_path / 'ledger', done=(str(second),))
