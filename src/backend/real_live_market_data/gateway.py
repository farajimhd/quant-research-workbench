from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl

from src.backend.real_live_market_data.bars import current_bar_row, rotate_minute_bar_if_needed
from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient
from src.backend.real_live_market_data.config import MarketGatewayConfig, market_gateway_config
from src.backend.real_live_market_data.features import apply_quote, apply_trade, market_status_row, signal_row_from_market
from src.backend.real_live_market_data.massive_ws import MassiveStocksWebSocket
from src.backend.real_live_market_data.models import QuoteEvent, SymbolState, TradeEvent, UniverseRecord, utc_now
from src.backend.real_live_market_data.persistence import ClickHouseReplayWriter
from src.backend.real_live_market_data.universe import load_universe_frame, universe_records


@dataclass
class MarketGateway:
    config: MarketGatewayConfig = field(default_factory=market_gateway_config)
    last_error: str = ""
    last_status_message: str = ""
    market_rows: list[dict[str, Any]] = field(default_factory=list)
    read_client: ClickHouseHttpClient | None = None
    running: bool = False
    signal_rows: list[dict[str, Any]] = field(default_factory=list)
    states: dict[str, SymbolState] = field(default_factory=dict)
    task: asyncio.Task | None = None
    universe: dict[str, UniverseRecord] = field(default_factory=dict)
    universe_frame: pl.DataFrame = field(default_factory=pl.DataFrame)
    write_client: ClickHouseHttpClient | None = None
    writer: ClickHouseReplayWriter | None = None
    ws: MassiveStocksWebSocket | None = None

    def load_universe(self) -> None:
        self.read_client = ClickHouseHttpClient(self.config.read_clickhouse)
        self.write_client = ClickHouseHttpClient(self.config.write_clickhouse)
        self.universe_frame = load_universe_frame(self.read_client, self.config)
        self.universe = universe_records(self.universe_frame)
        self.states = {ticker: self.states.get(ticker, SymbolState()) for ticker in self.universe}
        self.writer = ClickHouseReplayWriter(self.write_client, self.config.enable_clickhouse_writes)
        self.writer.initialize()
        self.last_status_message = f"Loaded {len(self.universe)} tradable symbols from ClickHouse read database."

    async def start(self) -> dict[str, Any]:
        if self.running:
            return self.status()
        if not self.universe:
            self.load_universe()
        if not self.config.massive.api_key:
            raise RuntimeError("MASSIVE_API_KEY is required for Massive websocket streaming.")
        if not self.config.websocket_enabled:
            self.last_status_message = "Market gateway websocket is disabled by REAL_LIVE_MARKET_WEBSOCKET_ENABLED."
            return self.status()
        symbols = sorted(self.universe)
        self.ws = MassiveStocksWebSocket(
            self.config.massive,
            symbols,
            on_event=self.handle_event,
            on_status=self.handle_status,
            subscribe_quotes=self.config.subscribe_quotes,
            subscribe_trades=self.config.subscribe_trades,
        )
        self.running = True
        self.task = asyncio.create_task(self._run_ws())
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self.running = False
        if self.ws:
            await self.ws.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.writer:
            self.writer.flush()
        self.task = None
        self.ws = None
        self.last_status_message = "Market gateway stopped."
        return self.status()

    async def _run_ws(self) -> None:
        if not self.ws:
            return
        try:
            await self.ws.run_forever()
        finally:
            self.running = False

    async def handle_status(self, item: dict[str, Any]) -> None:
        self.last_status_message = str(item.get("message") or item.get("status") or item)

    async def handle_event(self, event: TradeEvent | QuoteEvent) -> None:
        if not event.sym or event.sym not in self.universe:
            return
        state = self.states.setdefault(event.sym, SymbolState())
        if isinstance(event, TradeEvent):
            finalized_bar = rotate_minute_bar_if_needed(state, event)
            apply_trade(state, event)
            if self.writer:
                self.writer.add_bar(finalized_bar)
                self.writer.add_trade(event)
        else:
            apply_quote(state, event)
            if self.writer:
                self.writer.add_quote(event)

    def snapshot(self, row_limit: int | None = None) -> dict[str, Any]:
        if not self.universe:
            self.load_universe()
        now = self.snapshot_time()
        limit = row_limit or self.config.scanner_row_limit
        market_rows = [market_status_row(record, self.states.setdefault(ticker, SymbolState()), now) for ticker, record in self.universe.items()]
        market_rows.sort(key=lambda row: float(row.get("scanner_score") or 0), reverse=True)
        signal_rows = [row for row in (signal_row_from_market(row, now) for row in market_rows) if row]
        self.market_rows = market_rows[: max(limit, self.config.scanner_row_limit)]
        self.signal_rows = dedupe_signal_rows(signal_rows + self.signal_rows, self.config.signal_row_limit)
        return {
            "provider": "massive_ws_gateway",
            "session_date": now.astimezone(timezone.utc).date().isoformat(),
            "market_time": now.astimezone().strftime("%H:%M:%S"),
            "rows": self.signal_rows[:limit],
            "market_rows": self.market_rows[:limit],
            "row_count": min(limit, len(self.signal_rows)),
            "market_row_count": min(limit, len(self.market_rows)),
            "status": self.status(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "clickhouse_database": self.config.read_clickhouse.database,
            "clickhouse_url": self.config.read_clickhouse.endpoint_url,
            "clickhouse_writes": self.config.enable_clickhouse_writes,
            "last_error": self.last_error,
            "message": self.last_status_message,
            "running": self.running,
            "subscribe_quotes": self.config.subscribe_quotes,
            "subscribe_trades": self.config.subscribe_trades,
            "universe_loaded": bool(self.universe),
            "universe_symbols": len(self.universe),
            "write_clickhouse_database": self.config.write_clickhouse.database,
            "write_clickhouse_url": self.config.write_clickhouse.endpoint_url,
            "websocket_enabled": self.config.websocket_enabled,
            "websocket_url": self.config.massive.url,
        }

    def bars(self, symbol: str | None = None, row_limit: int = 500) -> dict[str, Any]:
        if not self.universe:
            self.load_universe()
        symbols = [symbol.upper()] if symbol else sorted(self.universe)
        rows: list[dict[str, Any]] = []
        for ticker in symbols:
            state = self.states.get(ticker)
            if not state:
                continue
            row = current_bar_row(ticker, state)
            if row:
                rows.append(row)
            if len(rows) >= row_limit:
                break
        return {
            "provider": "massive_ws_gateway",
            "timeframe": "1m",
            "rows": rows,
            "row_count": len(rows),
            "status": self.status(),
        }

    def snapshot_time(self) -> datetime:
        latest: datetime | None = None
        for state in self.states.values():
            event_time = state.last_trade.ts if state.last_trade else state.last_quote.ts if state.last_quote else None
            if event_time and (latest is None or event_time > latest):
                latest = event_time
        return latest or utc_now()


def dedupe_signal_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("live_signal_id") or f"{row.get('ticker')}|{row.get('signal_type')}")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped
