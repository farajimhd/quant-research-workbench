"""Exact fixed-episode, linear-capital hindsight oracle (not a live policy)."""
from bisect import bisect_left
from dataclasses import dataclass
import math

VERSION = 'hindsight-episode-oracle-v1'
MODES = {'long': ('long',), 'short': ('short',), 'long_short': ('long', 'short')}


@dataclass(frozen=True)
class Episode:
    key: str
    ticker: str
    listing_id: str
    side: str
    entry_us: int
    exit_us: int
    entry_price: float
    exit_price: float
    label_available_us: int


def positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')


def validate(episodes):
    keys = set()
    identities = {}
    for e in episodes:
        if not e.key or e.key in keys:
            raise ValueError('Duplicate or empty episode identity')
        keys.add(e.key)
        if not e.ticker or not e.listing_id or identities.setdefault(e.ticker, e.listing_id) != e.listing_id:
            raise ValueError('Missing or ambiguous listing identity')
        if e.side not in ('long', 'short'):
            raise ValueError('Invalid episode direction')
        if any(type(t) is not int for t in (e.entry_us, e.exit_us, e.label_available_us)):
            raise ValueError('Episode timestamps must be integer microseconds')
        if not 0 <= e.entry_us < e.exit_us <= e.label_available_us:
            raise ValueError('Invalid episode/availability ordering')
        positive(e.entry_price, 'Entry price')
        positive(e.exit_price, 'Exit price')


def economics(e, cost):
    """Same per-share costs and synthetic short reserve as Phase 2."""
    if not math.isfinite(cost) or cost < 0:
        raise ValueError('Cost must be finite and nonnegative')
    capital = e.entry_price + cost
    profit = (e.exit_price-e.entry_price) * (1 if e.side == 'long' else -1) - 2*cost
    factor = 1 + profit/capital
    if not all(math.isfinite(x) for x in (capital, profit, factor)):
        raise ValueError('Non-finite episode economics')
    return capital, profit, factor


def solve(episodes, *, initial_cash=10_000., cost_per_share=0., mode='long_short', check=None):
    """O(N log N) time/O(N) space. Exact recurrence, subject to float precision.

    Each dollar follows a path of nonoverlapping episodes. With linear returns,
    splitting dollars produces a weighted average of path multipliers, bounded
    by the best path. Therefore an all-capital path attains the continuous-size
    optimum. No diversification, capacity, margin calls or intermediate exits.
    """
    positive(initial_cash, 'Initial cash')
    validate(episodes)
    if mode not in MODES:
        raise ValueError('Unknown mode')
    economics(Episode('_', '_', '_', 'long', 0, 1, 1., 1., 1), cost_per_share)
    trades = sorted((e for e in episodes if e.side in MODES[mode]), key=lambda e: (e.entry_us, e.key))
    starts = [e.entry_us for e in trades]
    n = len(trades)
    best = [0.] * (n+1)  # log terminal wealth per dollar; waiting earns 1.
    chosen = [False] * n
    successors = [0] * n
    action_values = [None] * n
    for i in range(n-1, -1, -1):
        if check and i % 4096 == 0:
            check()
        e = trades[i]
        successor = bisect_left(starts, e.exit_us, lo=i+1)
        successors[i] = successor
        _, profit, factor = economics(e, cost_per_share)
        value = math.log1p(profit/(e.entry_price+cost_per_share)) + best[successor] if factor > 0 else None
        action_values[i] = value
        best[i] = best[i+1]
        if value is not None and value > best[i]:
            best[i] = value
            chosen[i] = True
    if best[0] + math.log(initial_cash) > math.log(float.fromhex('0x1.fffffffffffffp+1023')):
        raise ValueError('Terminal equity exceeds Float64; no truncated trajectory published')
    selected = []
    i = 0
    while i < n:
        if chosen[i]:
            selected.append(trades[i])
            i = successors[i]
        else:
            i += 1
    # Q-like targets are for FLAT states and committed episode actions only.
    labels = []
    for i, e in enumerate(trades):
        first = bisect_left(starts, e.entry_us)
        labels.append(dict(episode_key=e.key, entry_us=e.entry_us, exit_us=e.exit_us,
            episode_label_available_us=e.label_available_us, side=e.side, ticker=e.ticker,
            log_terminal_multiplier_if_taken=action_values[i],
            best_flat_log_terminal_multiplier=best[first],
            log_regret=None if action_values[i] is None else max(0., best[first]-action_values[i]),
            solvent_at_exit=action_values[i] is not None))
    ledger = replay(selected, initial_cash=initial_cash, cost_per_share=cost_per_share)
    final = ledger[-1]['cash_after'] if ledger else initial_cash
    if not math.isclose(math.log(final/initial_cash), best[0], rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError('Independent cash replay disagrees with optimal value')
    return dict(mode=mode, initial_cash=initial_cash, terminal_equity=final,
        net_profit=final-initial_cash, profit_to_initial_cash=final/initial_cash-1,
        log_terminal_multiplier=best[0], candidate_episodes=n, selected_episodes=len(selected),
        nonpositive_net_episodes=sum(economics(e,cost_per_share)[1] <= 0 for e in trades),
        total_entry_capital=math.fsum(r['capital_committed'] for r in ledger),
        total_cost=math.fsum(r['transaction_cost'] for r in ledger),
        ledger=ledger, action_labels=labels)


def replay(selected, *, initial_cash, cost_per_share):
    """Independent cash-flow replay; rows are variable-duration trade transitions."""
    cash = initial_cash
    previous_exit = -1
    ledger = []
    for e in selected:
        if e.entry_us < previous_exit:
            raise ValueError('Overlapping selected episodes')
        capital, _, _ = economics(e, cost_per_share)
        quantity = cash/capital
        committed = cash
        entry_net = e.entry_price + (cost_per_share if e.side == 'long' else -cost_per_share)
        exit_net = e.exit_price + (-cost_per_share if e.side == 'long' else cost_per_share)
        pnl = quantity * (exit_net-entry_net) * (1 if e.side == 'long' else -1)
        cash = committed+pnl
        if not math.isfinite(cash) or cash <= 0:
            raise ValueError('Selected schedule has insolvent/non-finite settlement')
        ledger.append(dict(episode_key=e.key, ticker=e.ticker, listing_id=e.listing_id,
            side=e.side, entry_us=e.entry_us, exit_us=e.exit_us, entry_price=e.entry_price,
            exit_price=e.exit_price, label_available_us=e.label_available_us,
            cash_before=committed, quantity=quantity, capital_committed=committed,
            cash_while_open=0., cash_after=cash, realized_pnl=pnl,
            transaction_cost=2*cost_per_share*quantity, holding_seconds=(e.exit_us-e.entry_us)/1e6))
        previous_exit = e.exit_us
    return ledger


def benchmark(episodes, *, allocation=1_000., initial_cash=10_000., cost_per_share=0., mode='long_short'):
    """Every episode once, equal entry allocation; independent lots may overlap."""
    positive(allocation, 'Benchmark allocation')
    positive(initial_cash, 'Initial cash')
    validate(episodes)
    economics(Episode('_', '_', '_', 'long', 0, 1, 1., 1., 1), cost_per_share)
    if mode not in MODES:
        raise ValueError('Unknown mode')
    events = {}
    profits = []
    for e in episodes:
        if e.side not in MODES[mode]:
            continue
        capital, profit, _ = economics(e, cost_per_share)
        pnl = allocation/capital * profit
        profits.append(pnl)
        # Exits fund entries at an equal timestamp. Aggregate each event batch.
        events.setdefault(e.entry_us, [[], []])[1].append(-allocation)
        events.setdefault(e.exit_us, [[], []])[0].append(allocation+pnl)
    balance = minimum = 0.
    for exits, entries in (events[t] for t in sorted(events)):
        balance = math.fsum((balance, math.fsum(exits)))
        minimum = min(minimum, balance)
        balance = math.fsum((balance, math.fsum(entries)))
        minimum = min(minimum, balance)
    required = -minimum
    profit = math.fsum(profits)
    total = allocation*len(profits)
    return dict(episode_count=len(profits), allocation_per_episode=allocation,
        total_entry_capital=total, total_profit=profit, required_initial_cash=required,
        profit_to_entry_capital=profit/total if total else None,
        profit_to_required_cash=profit/required if required else None,
        scaled_to_initial_cash=initial_cash,
        scaled_profit=profit*initial_cash/required if required else 0.,
        independent_overlapping_lots=True)
