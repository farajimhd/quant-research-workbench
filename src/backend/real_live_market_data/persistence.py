from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient, ensure_replay_tables
from src.backend.real_live_market_data.models import QuoteEvent, TradeEvent


@dataclass
class ClickHouseReplayWriter:
    client: ClickHouseHttpClient
    enabled: bool
    max_batch: int = 5000
    quote_rows: list[dict] = field(default_factory=list)
    trade_rows: list[dict] = field(default_factory=list)
    bar_rows: list[dict] = field(default_factory=list)

    def initialize(self) -> None:
        if self.enabled:
            ensure_replay_tables(self.client)

    def add_trade(self, event: TradeEvent) -> None:
        if not self.enabled:
            return
        self.trade_rows.append(
            {
                "session_date": event.ts.date().isoformat(),
                "ts": event.ts.isoformat(),
                "participant_ts": event.participant_ts.isoformat(),
                "trf_ts": event.trf_ts.isoformat(),
                "ingest_ts": event.ingest_ts.isoformat(),
                "sym": event.sym,
                "trade_id": event.trade_id,
                "seq": event.seq,
                "exchange": event.exchange,
                "tape": event.tape,
                "price": event.price,
                "size": event.size,
                "conditions": event.conditions,
                "trf_id": event.trf_id,
                "raw": json.dumps(event.raw, separators=(",", ":")),
            }
        )
        self.flush_if_needed()

    def add_quote(self, event: QuoteEvent) -> None:
        if not self.enabled:
            return
        self.quote_rows.append(
            {
                "session_date": event.ts.date().isoformat(),
                "ts": event.ts.isoformat(),
                "ingest_ts": event.ingest_ts.isoformat(),
                "sym": event.sym,
                "seq": event.seq,
                "bid_exchange": event.bid_exchange,
                "ask_exchange": event.ask_exchange,
                "bid_price": event.bid_price,
                "ask_price": event.ask_price,
                "bid_size": event.bid_size,
                "ask_size": event.ask_size,
                "conditions": event.conditions,
                "indicators": event.indicators,
                "tape": event.tape,
                "raw": json.dumps(event.raw, separators=(",", ":")),
            }
        )
        self.flush_if_needed()

    def add_bar(self, row: dict | None) -> None:
        if not self.enabled or not row:
            return
        self.bar_rows.append(row)
        self.flush_if_needed()

    def flush_if_needed(self) -> None:
        if len(self.trade_rows) >= self.max_batch or len(self.quote_rows) >= self.max_batch or len(self.bar_rows) >= self.max_batch:
            self.flush()

    def flush(self) -> None:
        if not self.enabled:
            return
        if self.trade_rows:
            self.client.insert_json_each_row("live_massive_trades", self.trade_rows)
            self.trade_rows = []
        if self.quote_rows:
            self.client.insert_json_each_row("live_massive_quotes", self.quote_rows)
            self.quote_rows = []
        if self.bar_rows:
            self.client.insert_json_each_row("live_market_bars", self.bar_rows)
            self.bar_rows = []
