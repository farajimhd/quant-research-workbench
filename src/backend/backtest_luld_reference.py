"""Read the preceding regular-session close through QMD's historical authority."""
from datetime import datetime, timedelta
from math import isfinite

from src.data_provider.calendar import mcal, MARKET_CALENDAR
from src.backend.qmd_gateway_client import QmdProductRequest, qmd_product_request, qmd_history_get_json

def previous_regular_close(ticker, session):
    schedule = mcal.get_calendar(MARKET_CALENDAR).schedule(
        start_date=session-timedelta(days=14), end_date=session-timedelta(days=1))
    if schedule.empty:
        return {}
    previous = schedule.index[-1].date()
    start = schedule.iloc[-1]['market_open'].to_pydatetime()
    end = schedule.iloc[-1]['market_close'].to_pydatetime()
    response = qmd_product_request(QmdProductRequest(
        'chart', authority='history', mode='backtest', ticker=ticker, timeframe='1m',
        start=start.isoformat(), end=end.isoformat(), as_of=end.isoformat(),
        stage='bars', include_structure=False, include_market_signals=False,
        limit=400, timeout_seconds=60), history_get=qmd_history_get_json)
    payload = response.payload
    rows = list(payload.get('bars') or payload.get('history') or [])
    if payload.get('current'):
        rows.append(payload['current'])
    eligible = []
    for row in rows:
        stamp = row.get('bar_end')
        if not stamp:
            continue
        at = datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
        close = float(row.get('close') or 0)
        if at.tzinfo and start < at <= end and isfinite(close) and close > 0 and row.get('trade_count',1) > 0:
            eligible.append((at,close))
    if not eligible:
        return {}
    at, close = max(eligible)
    return dict(price=close, session_date=previous.isoformat(),
        available_at=at.isoformat(), source='qmd_regular_session_last_trade')
