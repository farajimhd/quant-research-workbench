from datetime import date
from pathlib import Path

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
