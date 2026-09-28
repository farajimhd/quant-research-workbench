"""Read-only measurement of the actual running service's publication path."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

import websockets

from .evaluation_store import atomic_json, output_directory


async def observe(args):
    from .evaluation import percentile
    root = output_directory(args.output)
    tickers = set(args.tickers.upper().split(","))
    def get(path):
        with urllib.request.urlopen(args.url.rstrip("/") + path, timeout=10) as response:
            return json.load(response)
    before = await asyncio.to_thread(get, "/health")
    if args.model_id not in {row["model_id"] for row in before["models"]}:
        raise RuntimeError("requested model is not loaded")
    received = set()
    delays, delivery = [], []
    counts = {ticker: 0 for ticker in tickers}
    duplicates = 0
    start = time.monotonic()
    url = args.url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/stream/predictions"
    with (root / "observations.jsonl").open("x", encoding="utf-8") as log:
        async with websockets.connect(url, max_size=16*1024*1024) as socket:
            while (remaining := args.seconds - (time.monotonic() - start)) > 0:
                try:
                    row = json.loads(await asyncio.wait_for(socket.recv(), timeout=min(remaining, 10.)))
                except asyncio.TimeoutError:
                    continue
                if row.get("mode") not in {"live", "paper"} or row.get("model_id") != args.model_id or row.get("ticker") not in tickers:
                    continue
                key = (row["ticker"], row["event_at_us"])
                duplicates += key in received
                received.add(key)
                counts[row["ticker"]] += 1
                delay = (int(row["available_at_us"]) - int(row["event_at_us"])) / 1e6
                end_to_end = (time.time_ns()//1000 - int(row["event_at_us"])) / 1e6
                delays.append(delay)
                delivery.append(end_to_end)
                log.write(json.dumps({"ticker": row["ticker"], "origin": row["event_at_us"],
                                      "checkpoint_hash": row["checkpoint_hash"], "service_seconds": delay,
                                      "observer_seconds": end_to_end}) + "\n")
    after = await asyncio.to_thread(get, "/health")
    report = {"duration_seconds": time.monotonic()-start, "symbols_requested": len(tickers), "counts": counts,
              "duplicates": duplicates, "service_latency_seconds": {f"p{int(p*100)}": percentile(delays, p) for p in (.5,.95,.99)},
              "observer_latency_seconds": {f"p{int(p*100)}": percentile(delivery, p) for p in (.5,.95,.99)},
              "before": before, "after": after, "live_certified": False,
              "verdict": "observational evidence only; independently reconcile eligible market origins before certification"}
    atomic_json(root / "live_report.json", report)
    return report
