from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from src.backend.real_live_market_data.config import MassiveWebSocketConfig
from src.backend.real_live_market_data.models import QuoteEvent, TradeEvent, millis_to_utc, utc_now


MarketEventHandler = Callable[[TradeEvent | QuoteEvent], Awaitable[None]]
StatusHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MassiveStocksWebSocket:
    def __init__(self, config: MassiveWebSocketConfig, symbols: list[str], *, on_event: MarketEventHandler, on_status: StatusHandler, subscribe_quotes: bool = True, subscribe_trades: bool = True) -> None:
        self.config = config
        self.symbols = symbols
        self.on_event = on_event
        self.on_status = on_status
        self.subscribe_quotes = subscribe_quotes
        self.subscribe_trades = subscribe_trades
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("The websockets package is required for Massive websocket streaming. It is included by uvicorn[standard].") from exc

        while not self._stop.is_set():
            try:
                async with websockets.connect(self.config.url, ping_interval=20, ping_timeout=20, max_queue=20_000) as websocket:
                    await websocket.send(json.dumps({"action": "auth", "params": self.config.api_key}))
                    await self._subscribe(websocket)
                    async for message in websocket:
                        if self._stop.is_set():
                            break
                        await self._handle_message(message)
            except Exception as exc:
                await self.on_status({"status": "error", "message": str(exc)})
                await asyncio.sleep(3)

    async def stop(self) -> None:
        self._stop.set()

    async def _subscribe(self, websocket: Any) -> None:
        channels: list[str] = []
        if self.config.subscribe_all_symbols:
            if self.subscribe_trades:
                channels.append("T.*")
            if self.subscribe_quotes:
                channels.append("Q.*")
        else:
            for symbol in self.symbols:
                if self.subscribe_trades:
                    channels.append(f"T.{symbol}")
                if self.subscribe_quotes:
                    channels.append(f"Q.{symbol}")
        batch_size = max(1, self.config.subscribe_batch_size)
        for index in range(0, len(channels), batch_size):
            params = ",".join(channels[index : index + batch_size])
            await websocket.send(json.dumps({"action": "subscribe", "params": params}))
            await self.on_status({"status": "subscribed", "message": f"Subscribed {min(index + batch_size, len(channels))}/{len(channels)} channels."})

    async def _handle_message(self, message: str | bytes) -> None:
        payload = json.loads(message)
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            event_type = item.get("ev")
            if event_type in {"status", "auth_success"} or item.get("status"):
                await self.on_status(item)
            elif event_type == "T":
                await self.on_event(parse_trade(item))
            elif event_type == "Q":
                await self.on_event(parse_quote(item))


def parse_trade(item: dict[str, Any]) -> TradeEvent:
    return TradeEvent(
        conditions=[int(value) for value in item.get("c", []) if str(value).isdigit()],
        exchange=int(item.get("x") or 0),
        ingest_ts=utc_now(),
        participant_ts=millis_to_utc(item.get("pt")),
        price=float(item.get("p") or 0),
        raw=item,
        seq=int(item.get("q") or 0),
        size=float(item.get("s") or item.get("ds") or 0),
        sym=str(item.get("sym") or "").upper(),
        tape=int(item.get("z") or 0),
        trade_id=str(item.get("i") or ""),
        trf_id=int(item.get("trfi") or 0),
        trf_ts=millis_to_utc(item.get("trft")),
        ts=millis_to_utc(item.get("t")),
    )


def parse_quote(item: dict[str, Any]) -> QuoteEvent:
    return QuoteEvent(
        ask_exchange=int(item.get("ax") or item.get("ask_exchange") or 0),
        ask_price=float(item.get("ap") or item.get("ask_price") or 0),
        ask_size=int(float(item.get("as") or item.get("ask_size") or 0)),
        bid_exchange=int(item.get("bx") or item.get("bid_exchange") or 0),
        bid_price=float(item.get("bp") or item.get("bid_price") or 0),
        bid_size=int(float(item.get("bs") or item.get("bid_size") or 0)),
        conditions=[int(value) for value in item.get("c", []) if str(value).isdigit()],
        indicators=[int(value) for value in item.get("i", []) if str(value).isdigit()],
        ingest_ts=utc_now(),
        raw=item,
        seq=int(item.get("q") or 0),
        sym=str(item.get("sym") or "").upper(),
        tape=int(item.get("z") or 0),
        ts=millis_to_utc(item.get("t")),
    )
