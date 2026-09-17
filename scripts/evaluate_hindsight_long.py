"""Bounded single-ticker causal replay and post-run hindsight comparison.

Uses QMD History only, with the production strategy, Portfolio, OMS and simulated
broker. This is a development comparison, not a full-session acceptance backtest.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from dataclasses import replace
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from uuid import uuid4
from zoneinfo import ZoneInfo


def save(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def completed_positions(records):
    """Reconcile execution inventory; never convert an open position to a winner."""
    positions, active, inventory = [], None, 0.
    for record in records:
        p = record.payload
        quantity, price = float(p['size']), float(p['price'])
        side = {'B':'BUY', 'S':'SELL'}.get(p['side'], p['side'])
        if side not in ('BUY', 'SELL') or quantity <= 0:
            raise ValueError('Invalid execution side/quantity')
        at = record.event_time.timestamp()
        if side == 'BUY':
            if active is None:
                active = dict(entry_time=at, bought=0., sold=0., entry_cash=0., exit_cash=0., fees=0.)
            active['bought'] += quantity
            active['entry_cash'] += price*quantity
            inventory += quantity
        else:
            if active is None or quantity > inventory+1e-8:
                raise ValueError('Sell exceeds acquired inventory')
            active['sold'] += quantity
            active['exit_cash'] += price*quantity
            inventory -= quantity
        active['fees'] += float(p.get('commission') or 0)
        if abs(inventory) < 1e-8:
            positions.append(dict(entry_time=active['entry_time'], exit_time=at,
                entry_price=active['entry_cash']/active['bought'],
                exit_price=active['exit_cash']/active['sold'], quantity=active['bought'], fees=active['fees']))
            active = None
    return positions, dict(quantity=inventory, lifecycle=active)


async def run(args):
    from src.backend.qmd_gateway_client import QmdProductRequest, qmd_product_request, qmd_history_base_url
    from src.market_engine.historical_source import QmdHistoricalEventSource, _events_from_qmd_payload
    from src.market_engine.hindsight import PriceMacdLabels
    from src.market_engine.hindsight_long_review import compare_positions
    from src.market_engine.events import QuoteEvent
    from src.trading_runtime import strategy_engine as S, hindsight_long as H
    from src.trading_runtime.domain import InstrumentContract, TradingMode
    from src.trading_runtime.journal import TradingJournal
    from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner

    start = datetime.fromisoformat(f'{args.session}T{args.start}').replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
    end = datetime.fromisoformat(f'{args.session}T{args.end}').replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
    if not 0 < (end-start).total_seconds() <= 900 or end > datetime.now(timezone.utc):
        raise ValueError('Use a completed historical window of at most 15 minutes')
    if not ('04:00:00' <= args.start < args.end <= '20:00:00'):
        raise ValueError('Window must be inside 04:00-20:00 New York (HH:MM:SS)')
    root = args.runtime_root.resolve()
    if not root.is_dir():
        raise ValueError('Required runtime root is unavailable')
    if Path(__file__).resolve().parents[1] in root.parents or root == Path(__file__).resolve().parents[1]:
        raise ValueError('Runtime output cannot be in the repository')
    output = root/'hindsight-long'/str(uuid4())
    output.mkdir(parents=True)
    status = dict(status='active', ticker=args.ticker, start=start.isoformat(), end=end.isoformat(),
                  output=str(output), completed_events=0, failed=0, skipped=0, retried=0)
    save(output/'status.json', status)
    print(f'Active | {args.ticker} {args.session} {args.start}-{args.end} New York | canonical bars', flush=True)
    runtime = journal = None
    try:
        frames, provenance = [], {}
        for timeframe in ('100ms', '1s'):
            payload = await asyncio.to_thread(lambda tf=timeframe: qmd_product_request(QmdProductRequest(
                'chart', authority='history', mode='backtest', ticker=args.ticker, timeframe=tf,
                start=start.isoformat(), end=end.isoformat(), as_of=end.isoformat(), stage='bars',
                include_structure=False, include_market_signals=False, limit=50000,
                indicator_columns=('bar_start','bar_end','macd_line','macd_signal'), timeout_seconds=90)).payload)
            evidence = payload.get('indicator_provenance') or {}
            if payload.get('has_more') or not payload.get('indicators_available') or evidence.get('complete') is not True:
                raise RuntimeError(f'Incomplete canonical {timeframe} bars/MACD')
            provenance[timeframe] = evidence
            indicators = {row['bar_end']: row for row in payload.get('indicators', [])}
            for bar in payload.get('bars', []):
                at = datetime.fromisoformat(bar['bar_end'].replace('Z','+00:00'))
                if not start < at <= end:
                    raise RuntimeError('Bar timestamp outside requested window')
                frames.append((at, timeframe, bar, indicators.get(bar['bar_end'], {})))
        frames.sort(key=lambda row: (row[0], row[1]))
        if not frames:
            raise RuntimeError('No canonical frames in requested window')
        parameters = S.resolve_long_momentum_parameters(dict(hindsight_long_contract=H.CONTRACT,
            hindsight_long=dict(minimum_trail_bps=args.trail_bps, profit_giveback_fraction=args.giveback)), revision=47)
        assignment = S.StrategyAssignment('study', S.STRATEGY_ID, 47, 'study', args.ticker, 1,
            S.AssignmentStatus.WATCHING, S.StrategyPermissions(enter=True,reenter=True), parameters)
        strategy = S.AssignedLongMomentumStrategy([assignment])
        journal = TradingJournal(output/'journal.sqlite3')
        broker = SimulatedBrokerAdapter(['study'], mode=TradingMode.BACKTEST)
        runtime = TradingRuntime(RunConfig(mode=RunMode.BACKTEST, strategy_id=S.STRATEGY_ID,
            strategy_revision=47, account_ids=('study',), anchor_date=start.date(), run_id='study'),
            broker, strategy, journal, intent_planner=RuntimeIbkrStrategyOrderPlanner(
                {args.ticker: InstrumentContract('simulation-only:1',1,args.ticker,'STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        account = runtime.portfolio.states['study']
        account.profile = replace(account.profile, policy=replace(account.profile.policy, allow_outside_rth=True))
        await runtime.initialize()
        source = QmdHistoricalEventSource(qmd_history_base_url(), start=start, end=end,
            tickers=[args.ticker], batch_size=10000)
        labels, intervals, opened, direction = PriceMacdLabels(), [], None, None
        quote, index, last_report = None, 0, time.monotonic()

        def observation(at, price, **kw):
            return S.StrategyObservation(ticker=args.ticker, observed_at=at, price=price,
                bid=quote.bid_price if quote else 0., ask=quote.ask_price if quote else 0.,
                source_values={'market.spread_bps':dict(value=None,observed_at=quote.ts.isoformat() if quote else '')}, **kw)

        async def evaluate(obs):
            positions = await broker.positions('study')
            position = next((p for p in positions if p.conid == 1), None)
            await runtime.process_strategy_observation(replace(obs,
                position_quantity=float(position.position) if position else 0.,
                average_price=float(position.avgCost) if position else 0.))

        async def consume_frames(through):
            nonlocal index, opened, direction
            while index < len(frames) and frames[index][0] <= through:
                at, tf, bar, indicator = frames[index]
                index += 1
                await evaluate(observation(at, float(bar['close']),
                    bar_low=float(bar['low']), bar_high=float(bar['high']), bar_open=float(bar['open']),
                    macd_line=indicator.get('macd_line'), macd_signal=indicator.get('macd_signal'),
                    source_timeframe=tf, evaluation_events=('bar_close',)))
                if tf == '1s':
                    line, signal = indicator.get('macd_line'), indicator.get('macd_signal')
                    new = ('long' if line > signal else 'short' if line < signal else None) if line is not None and signal is not None else None
                    if new != direction:
                        if opened is not None:
                            intervals.append((opened,at.timestamp(),direction))
                        opened, direction = (at.timestamp() if new else None), new

        print('Active | canonical event replay | Portfolio + OMS + simulated broker', flush=True)
        async for rows in source.stream_rows():
            events = _events_from_qmd_payload(rows)
            for row, event in zip(rows, events, strict=True):
                # Bar [start,end) completes before the next event at end. Orders
                # therefore cannot fill against the event that caused a decision.
                await consume_frames(event.ts)
                await runtime.process_event(event, evaluate_strategy=False)
                if row['kind'] == 'trade':
                    labels.observe_payload(row)
                if isinstance(event, QuoteEvent):
                    quote = event
                    track = strategy.assignments()[0].state.get('hindsight_long', {})
                    active = strategy.assignments()[0].status in (S.AssignmentStatus.MANAGING,S.AssignmentStatus.ENTRY_PENDING,S.AssignmentStatus.EXIT_PENDING)
                    entry_window = event.ts.timestamp()-track.get('episode_open',0) < H.DEFAULTS['entry_window_ms']/1000
                    if active or entry_window:
                        await evaluate(observation(event.ts, event.midpoint,
                            source_timeframe='', evaluation_events=('market_data_update',)))
                status['completed_events'] += 1
                if status['completed_events'] > 1_000_000:
                    raise RuntimeError('One-million-event study budget exceeded; choose a shorter window')
            if time.monotonic()-last_report > 10:
                save(output/'status.json',status)
                print(f"Active | {status['completed_events']:,} events | {index:,}/{len(frames):,} bars", flush=True)
                last_report = time.monotonic()
        await consume_frames(end)
        if opened is not None and opened < end.timestamp():
            intervals.append((opened,end.timestamp(),direction))
        await runtime.finish()
        runtime = None
        benchmark = labels.result(intervals)
        records = journal.records('study')
        positions, open_position = completed_positions([r for r in records if r.category == 'execution'])
        report = compare_positions(benchmark['positions'], positions)
        source_files = ('src/trading_runtime/hindsight_long.py','src/trading_runtime/strategy_engine.py',
                        'src/trading_runtime/runtime.py','src/trading_runtime/simulated_broker.py',
                        'src/trading_runtime/strategy_orders.py','src/trading_runtime/execution_policies.py',
                        'src/market_engine/hindsight.py','src/market_engine/hindsight_long_review.py',
                        'scripts/evaluate_hindsight_long.py')
        report.update(ticker=args.ticker, start=start.isoformat(), end=end.isoformat(),
            source_revision=source.source_revision, indicator_provenance=provenance,
            parameters=parameters['hindsight_long'], positions=positions, open_position=open_position,
            net_closed_pnl=sum((p['exit_price']-p['entry_price'])*p['quantity']-p['fees'] for p in positions),
            decision_counts=dict(Counter(r.payload.get('reason') for r in records if r.category=='strategy_decision')),
            final_assignment=strategy.assignments()[0].payload(),
            source_hashes={p:hashlib.sha256((Path(__file__).resolve().parents[1]/p).read_bytes()).hexdigest() for p in source_files},
            study_limitations='Selected window; first observed bullish episode may already be in progress. '
                'Simulation-only contract id; no broker identity or production discovery acceptance. '
                'Open inventory is not force-filled at the study boundary; no holdout claim.')
        save(output/'report.json',report)
        status.update(status='completed',completed_positions=len(positions))
        save(output/'status.json',status)
        print(f"Completed | {len(positions)} closed trades | {report['matched_episode_count']}/{report['hindsight_long_count']} hindsight episodes matched | net ${report['net_closed_pnl']:.2f} | open shares {open_position['quantity']:g}", flush=True)
        print(f'Report: {output / "report.json"}', flush=True)
        return report
    except BaseException as exc:
        status.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,asyncio.CancelledError)) else 'failed',
                      failed=1, error=f'{type(exc).__name__}: {exc}')
        save(output/'status.json',status)
        raise
    finally:
        if runtime is not None:
            await runtime.finish('stopped')
        if journal is not None:
            journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker',required=True,type=str.upper)
    parser.add_argument('--session',required=True)
    parser.add_argument('--start',default='09:30:00')
    parser.add_argument('--end',default='09:35:00')
    parser.add_argument('--trail-bps',type=float,default=50.)
    parser.add_argument('--giveback',type=float,default=.25)
    parser.add_argument('--runtime-root',type=Path,default=Path(r'D:\TradingML\runtimes'))
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('Interrupted | partial journal retained; rerun starts a new study',file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f'Failed | {exc}',file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
