from datetime import date
from pathlib import Path
import json

from research.rl_trading.v6 import build_campaign
from research.rl_trading.v6.build_campaign import (DATES, day_arguments,
                                                    manifest_for)


def test_campaign_uses_actual_previous_trading_session_and_two_manifests():
    early, late = Path('early.json'), Path('late.json')
    assert len(DATES) == 19  # one context, 16 train, two development.
    assert manifest_for(date(2026, 8, 17), early, late) == early
    assert manifest_for(date(2026, 8, 18), early, late) == late
    args = day_arguments(date(2026, 8, 18), date(2026, 8, 17),
                         early=early, late=late, ledger=Path('ledger'),
                         output=Path('out'), workers=16)
    assert args[args.index('--manifest')+1] == str(late)
    assert args[args.index('--previous-manifest')+1] == str(early)
    assert args[args.index('--previous-date')+1] == '2026-08-17'
    first = day_arguments(date(2026, 7, 30), None, early=early, late=late,
                          ledger=Path('ledger'), output=Path('out'), workers=16)
    assert '--context-only' in first and '--previous-date' not in first


def test_controller_records_reused_day_root_without_rebuilding(monkeypatch,
                                                                  tmp_path):
    monkeypatch.setenv('QW_RUNTIME_ROOT', str(tmp_path))
    reuse = tmp_path / 'existing'
    reuse.mkdir()
    (reuse / 'complete.json').write_text('{"status":"complete"}')
    seen = []

    def fake_build(args):
        seen.append(args)
        destination = Path(args[args.index('--output')+1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'complete.json').write_text('{"status":"complete"}')

    monkeypatch.setattr(build_campaign, 'build_day', fake_build)
    root = tmp_path / 'campaign'
    assert build_campaign.main([
        '--early-manifest', str(tmp_path / 'early.json'),
        '--late-manifest', str(tmp_path / 'late.json'),
        '--ledger', str(tmp_path / 'ledger'), '--output-root', str(root),
        '--through', '2026-08-05',
        '--reuse-day-root', f'2026-08-05={reuse}']) == 0
    assert seen[-1][seen[-1].index('--output')+1] == str(reuse)
    assert json.loads((root / 'day-roots.json').read_text())[
        'day_roots']['2026-08-05'] == str(reuse)
