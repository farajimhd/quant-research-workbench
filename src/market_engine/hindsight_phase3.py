"""Finite long-only portfolio search over certified Phase 2 price-action rows."""
from dataclasses import dataclass, replace
from itertools import combinations, combinations_with_replacement
import math

VERSION = 'hindsight-phase3-long-grid-v1'


@dataclass(frozen=True, slots=True)
class Lot:
    ticker: str
    quantity: float
    entry_price: float
    capital_per_share: float
    entry_us: int


@dataclass(frozen=True, slots=True)
class Node:
    id: int | None
    parent_id: int | None
    cash: float
    lots: tuple[Lot, ...]
    actions: tuple[dict, ...]
    score: float
    equity: float
    realized_pnl: float
    order_count: int


@dataclass(frozen=True, slots=True)
class SearchConfig:
    initial_cash: float = 10_000.
    allocation_step: float = 2_500.
    max_lots: int = 4
    max_orders_per_second: int = 2
    max_candidates: int = 3  # 0 means all candidates.
    beam_width: int = 16  # 0 means no beam pruning.
    max_frontier: int = 100_000

    def validate(self):
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError('initial_cash must be finite and positive')
        if not math.isfinite(self.allocation_step) or not 0 < self.allocation_step <= self.initial_cash:
            raise ValueError('allocation_step must be finite and within initial cash')
        if any(type(v) is not int or v < lower for v, lower in (
                (self.max_lots,1),(self.max_orders_per_second,1),
                (self.max_candidates,0),(self.beam_width,0),(self.max_frontier,1))):
            raise ValueError('Search limits must be valid nonnegative integers')


def _finite_positive(value):
    return isinstance(value,(int,float)) and math.isfinite(value) and value > 0


def _rank(node):
    return (node.score,node.equity,node.cash,-node.order_count,
        tuple((x.ticker,x.entry_us,x.quantity) for x in node.lots))


def advance(frontier: list[Node], market_rows: list[dict], time_us: int,
            config: SearchConfig, *, terminal: bool = False) -> tuple[list[Node], dict]:
    """One exact action expansion, followed by explicit candidate/beam caps.

    Cash dominance among identical holdings is exact. Candidate and beam caps
    are heuristic and must be recorded as approximation in the output plan.
    """
    config.validate()
    long_rows = {r['ticker']:r for r in market_rows if r['side'] == 'long'}
    if len(long_rows) != sum(r['side'] == 'long' for r in market_rows):
        raise ValueError('Duplicate long market candidate')
    if not long_rows or any(r['time_us'] != time_us for r in long_rows.values()):
        raise ValueError('Incomplete or mismatched market second')
    eligible = [] if terminal else [r for r in long_rows.values() if
        r['can_open'] and r['open_value_available'] and _finite_positive(r['entry_price'])
        and _finite_positive(r['capital_per_share']) and
        isinstance(r['open_value_per_dollar'],(int,float)) and
        math.isfinite(r['open_value_per_dollar'])]
    eligible.sort(key=lambda r:(-r['open_value_per_dollar'],r['ticker']))
    pruned_candidates = max(0,len(eligible)-config.max_candidates) if config.max_candidates else 0
    if config.max_candidates:
        eligible = eligible[:config.max_candidates]
    best_by_lots = {}
    expanded = 0
    for parent in frontier:
        if parent.id is None:
            raise ValueError('Frontier node lacks a checkpoint id')
        closeable = [i for i,lot in enumerate(parent.lots) if
            lot.ticker in long_rows and long_rows[lot.ticker]['can_close'] and
            _finite_positive(long_rows[lot.ticker]['close_price'])]
        sell_sets = [tuple(range(len(parent.lots)))] if terminal else [()] + [
            subset for n in range(1,min(config.max_orders_per_second,len(closeable))+1)
            for subset in combinations(closeable,n)]
        if terminal and len(closeable) != len(parent.lots):
            continue
        for sold in sell_sets:
            remaining = tuple(lot for i,lot in enumerate(parent.lots) if i not in sold)
            cash = parent.cash
            realized = 0.
            sells = []
            for i in sold:
                lot = parent.lots[i]
                price = float(long_rows[lot.ticker]['close_price'])
                cash += lot.quantity*price
                profit = lot.quantity*(price-lot.entry_price)
                realized += profit
                sells.append(dict(action='sell',ticker=lot.ticker,quantity=lot.quantity,
                    price=price,capital=0.,realized_pnl=profit))
            room = min(config.max_lots-len(remaining),
                max(0,config.max_orders_per_second-len(sold)),
                max(0,int((cash+1e-8)//config.allocation_step)))
            buy_sets = [()] if terminal else [()] + [combo
                for n in range(1,room+1) for combo in combinations_with_replacement(range(len(eligible)),n)]
            for bought in buy_sets:
                current_lots = list(remaining)
                actions = list(sells)
                new_cash = cash
                for j in bought:
                    row = eligible[j]
                    amount = config.allocation_step
                    quantity = amount/float(row['capital_per_share'])
                    new_cash -= amount
                    current_lots.append(Lot(row['ticker'],quantity,float(row['entry_price']),
                        float(row['capital_per_share']),time_us))
                    actions.append(dict(action='buy',ticker=row['ticker'],quantity=quantity,
                        price=float(row['entry_price']),capital=amount,realized_pnl=0.))
                if new_cash < -1e-7:
                    continue
                current_lots.sort(key=lambda x:(x.ticker,x.entry_us,x.entry_price,x.quantity))
                score = equity = new_cash
                valid = True
                for lot in current_lots:
                    row = long_rows.get(lot.ticker)
                    if row is None or not _finite_positive(row['close_price']):
                        valid = False
                        break
                    equity += lot.quantity*float(row['close_price'])
                    future = row['hold_value_per_share'] if row['hold_value_available'] else (
                        float(row['close_price'])-lot.entry_price)
                    if future is None or not math.isfinite(future):
                        valid = False
                        break
                    score += lot.quantity*(float(row['close_price'])+future)
                if not valid:
                    continue
                candidate = Node(None,parent.id,new_cash,tuple(current_lots),tuple(actions),
                    score,equity,realized,parent.order_count+len(actions))
                expanded += 1
                key = candidate.lots
                previous = best_by_lots.get(key)
                if previous is None or candidate.cash > previous.cash+1e-8 or (
                    abs(candidate.cash-previous.cash) <= 1e-8 and _rank(candidate) > _rank(previous)):
                    best_by_lots[key] = candidate
    if not best_by_lots:
        raise ValueError('No feasible state remains; terminal liquidation may lack a price')
    ranked = sorted(best_by_lots.values(),key=_rank,reverse=True)
    pruned_beam = max(0,len(ranked)-config.beam_width) if config.beam_width else 0
    if config.beam_width:
        kept = ranked[:config.beam_width]
        flat = best_by_lots.get(())
        if flat is not None and flat not in kept:
            kept[-1] = flat
            kept.sort(key=_rank,reverse=True)
        ranked = kept
    if len(ranked) > config.max_frontier:
        raise ValueError('Exact frontier exceeds max_frontier; no optimality claim is possible')
    return ranked,dict(expanded=expanded,distinct=len(best_by_lots),
        candidate_pruned=pruned_candidates,beam_pruned=pruned_beam,
        retained=len(ranked))


def initial_node(initial_cash: float) -> Node:
    return Node(None,None,initial_cash,(),(),initial_cash,initial_cash,0.,0)


def with_id(node: Node, node_id: int) -> Node:
    return replace(node,id=node_id)
