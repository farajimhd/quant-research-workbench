from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any

from src.backend.real_live_market_data.gateway import MarketGateway


_gateway: MarketGateway | None = None


def get_market_gateway() -> MarketGateway:
    global _gateway
    if _gateway is None:
        _gateway = MarketGateway()
    return _gateway


async def market_gateway_start() -> dict[str, Any]:
    status = await get_market_gateway().start()
    await _notify_news_intelligence(
        active=True,
        session_id=str(status.get("trading_session_id") or ""),
        started_at_utc=str(status.get("started_at_utc") or ""),
    )
    return status


async def market_gateway_stop() -> dict[str, Any]:
    gateway = get_market_gateway()
    session_id = gateway.trading_session_id
    status = await gateway.stop()
    await _notify_news_intelligence(active=False, session_id=session_id, started_at_utc="")
    return status


async def _notify_news_intelligence(
    *, active: bool, session_id: str, started_at_utc: str
) -> None:
    """Synchronize the explicit live-trading gate without coupling startup.

    News intelligence being unavailable must not prevent the market gateway
    from starting or stopping; the service exposes its session state for an
    operator to verify and can be updated idempotently.
    """
    url = os.environ.get("NEWS_INTELLIGENCE_URL", "http://127.0.0.1:8804").rstrip("/")
    payload = {
        "active": active,
        "session_id": session_id,
        "started_at_utc": started_at_utc,
    }
    try:
        await asyncio.to_thread(_post_json, f"{url}/live-session", payload, 1.5)
    except Exception:
        return


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout):
        return


def market_gateway_status() -> dict[str, Any]:
    return get_market_gateway().status()


def market_gateway_universe_preview(
    row_limit: int = 0,
    *,
    refresh_enrichment: bool = False,
    snapshot_row_limit: int = 0,
    snapshot_sort_column: str = "",
    snapshot_sort_direction: str = "desc",
) -> dict[str, Any]:
    return get_market_gateway().universe_preview(
        row_limit=row_limit,
        refresh_enrichment=refresh_enrichment,
        snapshot_row_limit=snapshot_row_limit,
        snapshot_sort_column=snapshot_sort_column,
        snapshot_sort_direction=snapshot_sort_direction,
    )


def market_gateway_snapshot(row_limit: int = 500) -> dict[str, Any]:
    return get_market_gateway().snapshot(row_limit=row_limit)


def market_gateway_bars(symbol: str | None = None, row_limit: int = 500) -> dict[str, Any]:
    return get_market_gateway().bars(symbol=symbol, row_limit=row_limit)
