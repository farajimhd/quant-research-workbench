import json

from research.rl_trading.v6 import build_price_action_teacher_campaign as campaign


def test_campaign_orders_stages_and_never_opens_sealed_day(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    first = campaign.DATES[0]
    previous = campaign.CONTEXT_ONLY[0]
    roots = {}
    for day in (previous, first):
        root = runtime / str(day)
        root.mkdir()
        (root / 'complete.json').write_text('{}')
        roots[str(day)] = str(root)
    manifest = runtime / 'source.json'
    manifest.write_text(json.dumps({'version':
        'rl-trading-v6-forward-candle-day-roots', 'day_roots': roots}))
    calls = []
    monkeypatch.setattr(campaign, 'geometry_day',
                        lambda args: calls.append(('geometry', args)))
    monkeypatch.setattr(campaign, 'brackets_day',
                        lambda args: calls.append(('brackets', args)))
    monkeypatch.setattr(campaign, 'teacher_day',
                        lambda args: calls.append(('teacher', args)))
    output = runtime / 'output'
    output.mkdir()
    state = campaign.run_available(manifest, output, early=manifest,
        late=manifest, ledger=manifest, allocation_roots={})
    assert state['completed'] == [str(first)]
    assert state['queued'] == [str(day) for day in campaign.DATES[1:]]
    assert [name for name, _ in calls] == [
        'geometry', 'brackets', 'teacher']
    assert calls[2][1][calls[2][1].index('--previous-root') + 1] == (
        roots[str(previous)])
    assert '2026-08-26' not in json.dumps(state)
    calls.clear()
    resumed = campaign.run_available(manifest, output, early=manifest,
        late=manifest, ledger=manifest, allocation_roots={},
        done=(str(first),))
    assert resumed == state
    assert calls == []
