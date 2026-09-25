"""Build long-only cash-constrained Phase 3 teacher trajectories from Phase 2 V6."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
import math
import signal
import sqlite3
from time import monotonic

import polars as pl
from rich.console import Console

from research.rl_trading.v1.common import bounds, digest
from research.rl_trading.v1.market_values import MarketValues
from research.rl_trading.v1.phase3_search import (VERSION,Lot,Node,SearchConfig,
    advance,initial_node,with_id)
from src.market_engine.level_book_store import read,write
from src.runtime_paths import runtime_root

STOP = False


def file_hash(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _node_from_row(row):
    return Node(row['id'],row['parent_id'],row['cash'],
        tuple(Lot(**x) for x in json.loads(row['lots_json'])),
        tuple(json.loads(row['actions_json'])),row['score'],row['equity'],row['realized_pnl'],
        row['order_count'])


def _insert_node(db,node,time_us):
    result = db.execute('INSERT INTO nodes(time_us,parent_id,cash,lots_json,actions_json,score,equity,realized_pnl,order_count) '
        'VALUES(?,?,?,?,?,?,?,?,?)',(time_us,node.parent_id,node.cash,
        _json([asdict(x) for x in node.lots]),_json(node.actions),node.score,node.equity,
        node.realized_pnl,node.order_count))
    return with_id(node,result.lastrowid)


def _database(path,plan_hash,initial_cash):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY,time_us INTEGER NOT NULL,
            parent_id INTEGER,cash REAL NOT NULL,lots_json TEXT NOT NULL,actions_json TEXT NOT NULL,
            score REAL NOT NULL,equity REAL NOT NULL,realized_pnl REAL NOT NULL,
            order_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS frontier(position INTEGER PRIMARY KEY,node_id INTEGER NOT NULL);''')
    saved = db.execute("SELECT value FROM meta WHERE key='plan_hash'").fetchone()
    if saved:
        if saved['value'] != plan_hash:
            raise ValueError('Phase 3 checkpoint plan mismatch')
    else:
        db.execute("INSERT INTO meta VALUES('plan_hash',?)",(plan_hash,))
        db.execute("INSERT INTO meta VALUES('processed','-1')")
        db.execute("INSERT INTO meta VALUES('stats',?)",(_json(dict(expanded=0,candidate_pruned=0,universe_pruned=0,beam_pruned=0)),))
        root = _insert_node(db,initial_node(initial_cash),-1)
        db.execute('INSERT INTO frontier VALUES(0,?)',(root.id,))
        db.commit()
    return db


def _checkpoint(db,index,frontier,stats):
    db.execute('DELETE FROM frontier')
    db.executemany('INSERT INTO frontier(position,node_id) VALUES(?,?)',
        [(i,n.id) for i,n in enumerate(frontier)])
    db.execute("UPDATE meta SET value=? WHERE key='processed'",(str(index),))
    db.execute("UPDATE meta SET value=? WHERE key='stats'",(_json(stats),))
    db.commit()


def _restore(db):
    processed = int(db.execute("SELECT value FROM meta WHERE key='processed'").fetchone()['value'])
    stats = json.loads(db.execute("SELECT value FROM meta WHERE key='stats'").fetchone()['value'])
    rows = db.execute('SELECT n.* FROM frontier f JOIN nodes n ON n.id=f.node_id ORDER BY f.position').fetchall()
    if not rows:
        raise ValueError('Empty Phase 3 checkpoint frontier')
    return processed,[_node_from_row(r) for r in rows],stats


def _trajectory(db,best,first_us,seconds,initial_cash,optimality):
    chain = []
    node_id = best.id
    while node_id is not None:
        row = db.execute('SELECT * FROM nodes WHERE id=?',(node_id,)).fetchone()
        if row is None:
            raise ValueError('Broken Phase 3 search ancestry')
        chain.append((row['time_us'],_node_from_row(row)))
        node_id = row['parent_id']
    chain.reverse()
    if (len(chain) != seconds+1 or chain[0][0] != -1 or
            [t for t,_ in chain[1:]] != list(range(first_us,first_us+seconds*1_000_000,1_000_000))):
        raise ValueError('Phase 3 path is not a complete one-second grid')
    steps = []
    positions = []
    previous = chain[0][1]
    for index,(time_us,node) in enumerate(chain[1:]):
        done = index == seconds-1
        steps.append(dict(time_us=time_us,cash_before=previous.cash,
            cash_after=node.cash,equity_before=previous.equity,equity_after=node.equity,
            reward=node.equity-previous.equity,realized_pnl=node.realized_pnl,
            return_to_go=best.cash-node.equity,position_count_before=len(previous.lots),
            position_count_after=len(node.lots),cumulative_orders=node.order_count,
            done=done,next_time_us=None if done else time_us+1_000_000,
            optimality=optimality,
            action='wait' if not node.actions else 'trade',
            action_legs_json=_json(node.actions),positions_after_json=_json([asdict(x) for x in node.lots])))
        for lot in node.lots:
            positions.append(dict(time_us=time_us,**asdict(lot)))
        previous = node
    if not math.isclose(sum(x['reward'] for x in steps),best.cash-initial_cash,abs_tol=1e-5):
        raise ValueError('Phase 3 rewards do not reconcile to terminal cash')
    if positions:
        position_frame = pl.DataFrame(positions)
    else:
        position_frame = pl.DataFrame(schema=dict(time_us=pl.Int64,ticker=pl.String,
            quantity=pl.Float64,entry_price=pl.Float64,capital_per_share=pl.Float64,entry_us=pl.Int64))
    return pl.DataFrame(steps),position_frame


def _publish(path,frame):
    temporary = path.with_suffix('.parquet.tmp')
    frame.write_parquet(temporary,compression='zstd',statistics=True)
    if path.exists():
        if file_hash(path) != file_hash(temporary):
            temporary.unlink()
            raise ValueError('Existing Phase 3 output differs from rebuilt result')
        temporary.unlink()
    else:
        temporary.replace(path)
    return dict(file=path.name,rows=frame.height,file_hash=file_hash(path))


def run(args,console):
    source = args.phase2.resolve()
    source_plan = read(source/'plan.json')
    source_complete = read(source/'complete.json')
    if source_plan['version'] != 'hindsight-greedy-fractional-v6' or source_plan.get('valuation_basis') != 'price_action':
        raise ValueError('Phase 3 requires certified price-action Phase 2 V6')
    if source_complete['plan_hash'] != source_plan['plan_hash']:
        raise ValueError('Phase 2 completion does not match its plan')
    if source_plan.get('liquidation_us') is None:
        raise ValueError('Phase 2 lacks the 19:58 liquidation boundary')
    config = SearchConfig(args.initial_cash,args.allocation_step,args.max_lots,
        args.max_orders_per_second,args.max_candidates,args.beam_width,args.max_frontier,args.top_n)
    config.validate()
    left,_ = bounds(date.fromisoformat(source_plan['date']))
    cutoff = source_plan['liquidation_us']
    if cutoff-left != 57_480_000_000:
        raise ValueError('Unexpected Phase 2 19:58 liquidation time')
    if args.start_second < 0 or args.start_second >= 57480:
        raise ValueError('start-second must be before 19:58 ET')
    end_second = 57480 if args.end_second is None else args.end_second
    if not args.start_second < end_second <= 57480:
        raise ValueError('end-second must follow start and not exceed 19:58 ET')
    first_us = left+args.start_second*1_000_000
    seconds = end_second-args.start_second+1
    runtime = runtime_root()
    if not runtime.is_dir():
        raise ValueError(f'Required runtime root unavailable: {runtime}')
    plan = dict(version=VERSION,phase2_root=str(source),phase2_plan_hash=source_plan['plan_hash'],
        phase2_plan_file_hash=file_hash(source/'plan.json'),
        phase2_complete_file_hash=file_hash(source/'complete.json'),date=source_plan['date'],
        phase2_tensor_file_hashes={name:source_complete['files'][name] for name in (
            source_complete['tensor']['holding']['file'],source_complete['tensor']['opening']['file'])},
        scope=source_plan['scope'],mode='long',first_us=first_us,end_us=left+end_second*1_000_000,
        true_session_cutoff_us=cutoff,config=asdict(config),
        optimality='proven_within_grid' if not config.beam_width and not config.max_candidates else 'approximate_beam',
        observation_contract='Join only causal current-time market features; never use Phase 2 targets or values as model observations',
        reward_contract='Change in marked portfolio equity, including costs; terminal cash minus initial cash',
        code_hashes={p:file_hash(REPO/p) for p in (
            'research/rl_trading/v1/build_phase3.py','research/rl_trading/v1/phase3_search.py',
            'research/rl_trading/v1/market_values.py','research/rl_trading/v1/common.py',
            'research/rl_trading/v1/universe.py')})
    plan['plan_hash'] = digest(plan)
    root = runtime/'hindsight-phase3'/plan['date']/plan['plan_hash'][:16]
    root.mkdir(parents=True,exist_ok=True)
    write(root/'plan.json',plan)
    complete_path = root/'complete.json'
    if complete_path.exists():
        complete = read(complete_path)
        if complete['plan_hash'] != plan['plan_hash'] or any(
                file_hash(root/name) != certificate['file_hash'] for name,certificate in complete['files'].items()):
            raise ValueError('Phase 3 completion integrity failure')
        console.print(f"Reused verified Phase 3: {root}")
        return 0
    console.print(f"Phase 3 | {plan['date']} | long-only | {seconds:,} seconds | {len(source_plan['selected']):,} listings | top {config.top_n or 'all'} plus held")
    console.print(f"Cash ${config.initial_cash:,.2f}; allocation unit ${config.allocation_step:,.2f}; beam {config.beam_width or 'unbounded'}; candidate cap {config.max_candidates or 'all'}")
    console.print(f"Optimality: {plan['optimality']} | output: {root}",soft_wrap=True)
    started = monotonic()
    db = _database(root/'search.sqlite3',plan['plan_hash'],config.initial_cash)
    previous_handler = signal.signal(signal.SIGINT,lambda *_:request_stop())
    try:
        processed,frontier,totals = _restore(db)
        with MarketValues(source) as market:
            db.execute('BEGIN')
            for index in range(processed+1,seconds):
                if STOP or (root/'STOP').exists():
                    db.rollback()
                    write(root/'progress.json',dict(status='interrupted',processed=processed+1,
                        total=seconds,frontier=len(frontier),stats=totals),immutable=False)
                    return 2
                time_us = first_us+index*1_000_000
                if config.top_n:
                    held = {lot.ticker for node in frontier for lot in node.lots}
                    snapshot,eligible_count = market.at_subset(time_us,config.top_n,held)
                    if index == seconds-1:
                        eligible_count = 0
                    candidates,stats = advance(frontier,snapshot.to_dicts(),time_us,
                        config,terminal=index==seconds-1,
                        full_eligible_count=eligible_count)
                else:
                    candidates,stats = advance(frontier,market.at(time_us).to_dicts(),time_us,
                        config,terminal=index==seconds-1)
                frontier = [_insert_node(db,n,time_us) for n in candidates]
                for key in totals:
                    totals[key] += stats[key]
                if (index+1)%60 == 0 or index == seconds-1:
                    _checkpoint(db,index,frontier,totals)
                    processed = index
                    write(root/'progress.json',dict(status='running',processed=index+1,
                        total=seconds,frontier=len(frontier),stats=totals,
                        elapsed_seconds=monotonic()-started),immutable=False)
                    if index == seconds-1 or monotonic()-started > 5 and (index+1)%600 == 0:
                        console.print(f"Processed {index+1:,}/{seconds:,} | active {len(frontier):,} | beam-pruned {totals['beam_pruned']:,} | {monotonic()-started:.1f}s")
                    if index != seconds-1:
                        db.execute('BEGIN')
        if any(n.lots for n in frontier):
            raise ValueError('Phase 3 terminal frontier contains an open position')
        best = max(frontier,key=lambda n:(n.cash,-n.order_count,n.id))
        steps,positions = _trajectory(db,best,first_us,seconds,config.initial_cash,
            plan['optimality'])
        trajectory = _publish(root/'trajectory.parquet',steps)
        position_file = _publish(root/'positions_after.parquet',positions)
        if (file_hash(source/'plan.json') != plan['phase2_plan_file_hash'] or
            file_hash(source/'complete.json') != plan['phase2_complete_file_hash'] or
            any(file_hash(source/name) != expected for name,expected in plan['phase2_tensor_file_hashes'].items())):
            raise ValueError('Phase 2 source changed during Phase 3 build')
        completion = dict(plan_hash=plan['plan_hash'],files={x['file']:x for x in (trajectory,position_file)},
            seconds=seconds,initial_cash=config.initial_cash,terminal_cash=best.cash,
            terminal_profit=best.cash-config.initial_cash,
            profit_to_initial_cash=(best.cash-config.initial_cash)/config.initial_cash,
            optimality=plan['optimality'],stats=totals)
        write(complete_path,completion)
        write(root/'progress.json',dict(status='complete',processed=seconds,total=seconds,
            frontier=len(frontier),stats=totals,elapsed_seconds=monotonic()-started),immutable=False)
        console.print(f"Complete | terminal ${best.cash:,.2f} | profit ${best.cash-config.initial_cash:,.2f} | {plan['optimality']}")
        return 0
    finally:
        signal.signal(signal.SIGINT,previous_handler)
        db.close()


def request_stop():
    global STOP
    STOP = True


def main(argv=None):
    global STOP
    STOP = False
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2',type=Path,required=True)
    parser.add_argument('--initial-cash',type=float,default=10_000.)
    parser.add_argument('--allocation-step',type=float,default=2_500.)
    parser.add_argument('--max-lots',type=int,default=4)
    parser.add_argument('--max-orders-per-second',type=int,default=4)
    parser.add_argument('--max-candidates',type=int,default=3,
        help='Top Phase 2 opening values considered per second; 0 considers all')
    parser.add_argument('--beam-width',type=int,default=16,
        help='Maximum portfolio states retained per second; 0 disables beam pruning')
    parser.add_argument('--max-frontier',type=int,default=100_000)
    parser.add_argument('--top-n',type=int,default=100,
        help='Maximum visible tickers including all holdings; ranked by completed 60s volume')
    parser.add_argument('--start-second',type=int,default=0,help='Offset from 04:00 ET; for bounded canaries')
    parser.add_argument('--end-second',type=int,default=None,help='Offset from 04:00 ET; default 19:58')
    return run(parser.parse_args(argv),Console())


if __name__ == '__main__':
    raise SystemExit(main())
