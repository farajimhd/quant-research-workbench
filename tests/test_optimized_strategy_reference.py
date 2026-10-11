"""Comparison admission and coverage invariants, without running a backtest."""
import pytest

from src.trading_runtime.optimized_strategy_reference import (
    OptimizationReference, compare_sessions,
)


def reference():
    return OptimizationReference(24, 'sealed-winner', 872, {}, {}, {
        f'2026-08-{day:02d}': {'net_pnl': 100., 'closed_positions': 2,
                             'terminal_valid': True}
        for day in range(1, 31)
    })


def result():
    return dict(winner_sha256='sealed-winner', run_id='actual-app-run',
                strategy_digest='sealed-app-release', full_session=True,
                market_cursor_verified=True, verified_sequence=40,
                metrics={'net_pnl': 92., 'closed_positions': 2,
                         'terminal_valid': True})


def test_partial_comparison_keeps_every_reference_session():
    rows = compare_sessions(reference(), {'2026-08-01': result()})
    assert len(rows) == 30
    assert rows[0]['differences'] == {'net_pnl': -8., 'closed_positions': 0}
    assert rows[0]['status'] == 'compared'
    assert all(row['status'] == 'not_run' and row['app'] is None for row in rows[1:])


@pytest.mark.parametrize('field,value', [
    ('winner_sha256', 'another-winner'), ('full_session', False),
    ('market_cursor_verified', False), ('verified_sequence', 0),
    ('run_id', ''), ('strategy_digest', ''),
])
def test_unverified_app_results_are_not_comparison_authority(field, value):
    actual = result(); actual[field] = value
    with pytest.raises(ValueError, match='Unverified'):
        compare_sessions(reference(), {'2026-08-01': actual})


def test_missing_metrics_remain_visible_and_nan_fails():
    actual = result(); actual['metrics'].pop('closed_positions')
    row = compare_sessions(reference(), {'2026-08-01': actual})[0]
    assert row['status'] == 'incomplete_metrics'
    assert row['missing_metrics'] == ['closed_positions']
    actual['metrics']['net_pnl'] = float('nan')
    with pytest.raises(ValueError, match='Non-finite'):
        compare_sessions(reference(), {'2026-08-01': actual})


def test_non_reference_session_is_rejected():
    with pytest.raises(ValueError, match='non-reference'):
        compare_sessions(reference(), {'2027-01-01': result()})
