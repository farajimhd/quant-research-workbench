"""Deterministic whole-ticker ownership and chronological month execution."""

from collections import defaultdict, deque
import heapq

from ..shards.contract import digest


def assign(units, jobs, workers, *, heavy_first=False):
    """Never split a ticker between slots; preserve every frozen unit verbatim."""
    if workers < 1:
        raise ValueError("Positive worker count required")
    grouped = defaultdict(list)
    priorities = {}
    for rank, unit in enumerate(units):
        ticker = unit["ticker"]
        priorities.setdefault(ticker, rank)
        grouped[ticker].append(unit)
    jobs = {j["ticker"]: j for j in jobs}
    costs = {}
    for ticker, months in grouped.items():
        if len({u["month"] for u in months}) != len(months):
            raise ValueError("Duplicate ticker-month assignment")
        by_year = {int(y["year"]): y for y in jobs[ticker]["years"]}
        costs[ticker] = sum(
            max(1, int(by_year[int(u.get("year", u["month"][:4]))].get("events", 1)) // 12)
            for u in months
        )
    loads = [(0, slot) for slot in range(workers)]
    heapq.heapify(loads)
    owners = [[] for _ in range(workers)]
    for ticker in sorted(grouped, key=lambda t: (-costs[t], priorities[t], t)):
        load, slot = heapq.heappop(loads)
        owners[slot].append(ticker)
        heapq.heappush(loads, (load + costs[ticker], slot))
    queues, manifests = [], []
    for slot, tickers in enumerate(owners):
        ordered = sorted(tickers, key=lambda t: (-costs[t], priorities[t], t) if heavy_first else (priorities[t], t))
        tasks = [u for ticker in ordered for u in sorted(grouped[ticker], key=lambda u: u["month"])]
        queues.append(deque(tasks))
        manifests.append(dict(slot=slot, tasks=len(tasks), tickers=ordered,
                              estimated_events=sum(costs[t] for t in ordered),
                              unit_hash=digest(tasks)))
    if sum(map(len, queues)) != sum(map(len, grouped.values())):
        raise ValueError("Assignment coverage differs from frozen units")
    return queues, manifests
