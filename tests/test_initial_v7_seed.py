"""First-session state requires full canonical absence, never missing coverage alone."""
import pytest
from pipelines.strategy_one.initial_v7_seed import SOURCE_FILTER, certify_absence


def proof(**changes):
    args = dict(session='2026-08-07', ticker='BSEM',
                certificates=[dict(source_date='2026-08-06', source_filter_key=SOURCE_FILTER)],
                expected_days=['2026-08-06'], metadata_days=0, prior_events=0)
    args.update(changes)
    return certify_absence(**args)


def test_complete_absent_prefix_is_initial_state():
    assert proof()['prior_event_count'] == 0


def test_quotes_are_recorded_but_cannot_create_v7_price_history():
    assert proof(prior_quotes=2)['prior_quote_count'] == 2


@pytest.mark.parametrize('changes', [dict(prior_events=1), dict(metadata_days=1),
    dict(expected_days=['2026-08-05', '2026-08-06']), dict(certificates=[]),
    dict(certificates=[dict(source_date='2026-08-06', source_filter_key='ticker-filtered')])])
def test_missing_or_nonempty_authority_fails(changes):
    with pytest.raises(ValueError):
        proof(**changes)
