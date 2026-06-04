from __future__ import annotations

from typing import Any

from src.backend.real_live_market_data.gateway import MarketGateway


_gateway: MarketGateway | None = None


def get_market_gateway() -> MarketGateway:
    global _gateway
    if _gateway is None:
        _gateway = MarketGateway()
    return _gateway


async def market_gateway_start() -> dict[str, Any]:
    return await get_market_gateway().start()


async def market_gateway_stop() -> dict[str, Any]:
    return await get_market_gateway().stop()


def market_gateway_status() -> dict[str, Any]:
    return get_market_gateway().status()


def market_gateway_universe_preview(row_limit: int = 0, *, refresh_enrichment: bool = False) -> dict[str, Any]:
    return get_market_gateway().universe_preview(row_limit=row_limit, refresh_enrichment=refresh_enrichment)


def market_gateway_snapshot(row_limit: int = 500) -> dict[str, Any]:
    return get_market_gateway().snapshot(row_limit=row_limit)


def market_gateway_bars(symbol: str | None = None, row_limit: int = 500) -> dict[str, Any]:
    return get_market_gateway().bars(symbol=symbol, row_limit=row_limit)
