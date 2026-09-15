"""Causal top-of-book pressure; bounded 100ms buckets, no raw-event retention."""
from collections import deque
from datetime import datetime
from math import isfinite

from src.market_engine.events import QuoteEvent, TradeEvent

CONTRACT = "trade-nbbo-pressure-v1"
DEFAULT_POLICY = {"enabled": True, "minimum_trades": 3, "minimum_classified_fraction": .5,
                  "trade_imbalance": .3, "quote_imbalance": .2,
                  "retreat_spreads": 1., "confirmation_ms": 200}


class PressureTracker:
    def __init__(self, saved=None, *, extra_windows=None):
        saved = saved or {}
        stored_windows = saved.get("extra_windows", {})
        if extra_windows is not None and saved and extra_windows != stored_windows:
            raise ValueError("Cannot change pressure windows when restoring a checkpoint")
        self.extra_windows = dict(stored_windows if extra_windows is None else extra_windows)
        for name, seconds in self.extra_windows.items():
            if not isinstance(name, str) or not name.startswith("window_") or type(seconds) is not int or not 1 <= seconds <= 60:
                raise ValueError("Pressure window names must start with window_ and durations must be 1-60 integer seconds")
        self.retention_buckets = max(3, *self.extra_windows.values()) * 10 if self.extra_windows else 30
        self.buckets = deque(saved.get("buckets", []), maxlen=self.retention_buckets + 1)
        self.quote = saved.get("quote")
        self.last_time = saved.get("last_time", -1.)
        self.rejected = saved.get("rejected", 0)

    def checkpoint(self):
        saved = dict(buckets=list(self.buckets), quote=self.quote, last_time=self.last_time, rejected=self.rejected)
        if self.extra_windows:
            saved["extra_windows"] = dict(self.extra_windows)
        return saved

    def observe(self, event):
        now = event.ts.timestamp()
        if now < self.last_time:
            self.rejected += 1
            return
        self.last_time = now
        index = int(now * 10)
        while self.buckets and self.buckets[0]["index"] < index - self.retention_buckets:
            self.buckets.popleft()
        if not self.buckets or self.buckets[-1]["index"] != index:
            self.buckets.append(dict(index=index, first=None, last=None, high=0., low=None, count=0,
                                     total=0., buy=0., sell=0., ofi=0., depth=0.))
        b = self.buckets[-1]
        if isinstance(event, QuoteEvent):
            values = (event.bid_price, event.ask_price, event.bid_size, event.ask_size)
            if not all(isfinite(v) for v in values) or not (0 < event.bid_price < event.ask_price and min(event.bid_size, event.ask_size) >= 0):
                self.quote = None
                return
            q = dict(time=now, bid=event.bid_price, ask=event.ask_price, bs=event.bid_size, ass=event.ask_size)
            p = self.quote
            if p and 0 <= now - p["time"] <= 1:
                # Standard best-quote order-flow imbalance: movements and size changes.
                b["ofi"] += ((q["bs"] if q["bid"] >= p["bid"] else 0) - (p["bs"] if q["bid"] <= p["bid"] else 0)
                             - (q["ass"] if q["ask"] <= p["ask"] else 0) + (p["ass"] if q["ask"] >= p["ask"] else 0))
                b["depth"] += q["bs"] + q["ass"] + p["bs"] + p["ass"]
            self.quote = q
        elif isinstance(event, TradeEvent) and event.price_eligible and event.raw.get("volume_eligible") is not False:
            if not all(isfinite(v) and v > 0 for v in (event.price, event.size)):
                return
            b["first"] = event.price if b["first"] is None else b["first"]
            b["last"] = event.price
            b["high"] = max(b["high"], event.price)
            b["low"] = min(b["low"], event.price) if b.get("low") is not None else event.price
            b["count"] += 1
            b["total"] += event.size
            q = self.quote
            if q and 0 <= now - q["time"] <= 1:
                if event.price >= q["ask"]:
                    b["buy"] += event.size
                elif event.price <= q["bid"]:
                    b["sell"] += event.size
                # Inside-spread trades remain unknown, not guessed using future ticks.

    def snapshot(self, at: datetime, *, include_totals=False):
        now = at.timestamp()
        q = self.quote
        result = dict(contract=CONTRACT, observed_at=at.isoformat(), ready=False, rejected_out_of_order=self.rejected)
        if not q or not 0 <= now - q["time"] <= 1 or self.last_time > now:
            return result
        prior = [b for b in self.buckets if now-1 <= b["index"]/10 < int(now*10)/10 and b["count"]]
        if prior:
            result["micro"] = dict(high=max(b["high"] for b in prior), low=min(b.get("low") or b["first"] for b in prior),
                trades=sum(b["count"] for b in prior), span_ms=(prior[-1]["index"]-prior[0]["index"])*100)
        spread = q["ask"] - q["bid"]
        result.update(quote_age_ms=(now-q["time"])*1000, spread=spread)
        for name, seconds in (("fast", 1), ("slow", 3), *self.extra_windows.items()):
            # Drop the partial left-edge bucket: never include events older than the window.
            rows = [b for b in self.buckets if now-seconds <= b["index"]/10 <= now]
            trades = [b for b in rows if b["count"]]
            total = sum(b["total"] for b in rows)
            buy, sell = (sum(b[key] for b in rows) for key in ("buy", "sell"))
            depth = sum(b["depth"] for b in rows)
            result[name] = dict(trades=sum(b["count"] for b in rows),
                classified_fraction=(buy+sell)/total if total else 0.,
                trade_imbalance=(buy-sell)/(buy+sell) if buy+sell else 0.,
                quote_imbalance=max(-1., min(1., 2*sum(b["ofi"] for b in rows)/depth)) if depth else 0.,
                quote_ready=depth > 0,
                progress_spreads=(trades[-1]["last"]-trades[0]["first"])/spread if trades else 0.,
                retreat_spreads=(max(b["high"] for b in trades)-trades[-1]["last"])/spread if trades else 0.)
            if include_totals:
                high = max((b["high"] for b in trades), default=None)
                low = min((b.get("low") or b["first"] for b in trades), default=None)
                first, last = (trades[0]["first"], trades[-1]["last"]) if trades else (None, None)
                span = high - low if trades else 0.
                result[name].update(window_seconds=seconds, total_volume=total, buy_volume=buy,
                    sell_volume=sell, unknown_volume=total-buy-sell, signed_volume=buy-sell,
                    order_flow_imbalance=sum(b["ofi"] for b in rows), quote_depth_sum=depth,
                    first=first, last=last, high=high, low=low,
                    body_to_range=(last-first)/span if span else None,
                    close_location=(last-low)/span if span else None)
        result["ready"] = True
        return result


def evaluate(policy, state, observation):
    """One shared entry/exit/reentry interpretation, independent of MACD."""
    p = {**DEFAULT_POLICY, **policy}
    evidence = dict(observation.market_pressure)
    try:
        age = (observation.observed_at-datetime.fromisoformat(evidence["observed_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = -1
    ready = evidence.get("contract") == CONTRACT and evidence.get("ready") and 0 <= age <= .25
    fast = evidence.get("fast", {})
    ready = bool(ready and fast.get("trades", 0) >= p["minimum_trades"] and fast.get("classified_fraction", 0) >= p["minimum_classified_fraction"] and fast.get("quote_ready"))
    selling = ready and fast["trade_imbalance"] <= -p["trade_imbalance"] and fast["quote_imbalance"] <= -p["quote_imbalance"] and fast["retreat_spreads"] >= p["retreat_spreads"]
    absorption = ready and fast["trade_imbalance"] >= p["trade_imbalance"] and fast["quote_imbalance"] <= -p["quote_imbalance"] and fast["progress_spreads"] <= 0 and fast["retreat_spreads"] >= p["retreat_spreads"]
    adverse = bool(selling or absorption)
    now = observation.observed_at.timestamp()
    previous = state.get("pressure_evaluated_at", now)
    if not 0 <= now-previous <= .25:
        state.pop("pressure_adverse_since", None)
    state["pressure_evaluated_at"] = now
    if adverse:
        state.setdefault("pressure_adverse_since", now)
    else:
        state.pop("pressure_adverse_since", None)
    confirmed = adverse and 0 <= now-state["pressure_adverse_since"] and (now-state["pressure_adverse_since"])*1000 >= p["confirmation_ms"]
    recovered = ready and not adverse and fast["trade_imbalance"] >= 0 and fast["progress_spreads"] > 0 and fast["quote_imbalance"] >= 0
    return dict(**evidence, usable=ready, adverse=adverse, exit_confirmed=bool(confirmed), recovered=bool(recovered),
                reason="seller_absorption" if absorption else "selling_pressure" if selling else "clear" if ready else "unavailable")
