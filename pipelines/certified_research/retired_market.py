"""User-retired duplicate market products; source repair remains independent."""

TABLES=('research_bars_v1','research_indicators_v1','research_indicator_state_v1',
        'research_liquidity_v1','research_activity_v1','research_stage_bars_v1',
        'research_stage_indicators_v1','research_stage_liquidity_v1')
PRODUCTS=('bars','technical','technical_state','broker_100ms','activity')
MESSAGE='The duplicate certified-research market pipeline is retired. Use run_market_data_pipeline.py backfill --step bars.'


def require_available(database):
    if database=='arte':raise RuntimeError(MESSAGE)
