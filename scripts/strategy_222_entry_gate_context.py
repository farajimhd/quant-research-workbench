"""Join exact delivered candle/VWAP context to the completed base audit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter
from datetime import datetime
import json
from math import isfinite, isclose
from pathlib import Path
import sqlite3

from strategy_222_supervised_research import digest, save


def frame_context(bar, indicator, *, at, price, minimum_body_bps):
    for value in (bar, indicator):
        stamp = datetime.fromisoformat(value['bar_end'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.timestamp() != at:
            raise ValueError('Frame is not the exact completed decision candle')
    opened, close, vwap = bar['open'], bar['close'], indicator.get('execution_vwap')
    if not all(type(v) in (int, float) and isfinite(v) and v > 0 for v in (opened, close, price)):
        raise ValueError('Invalid candle prices')
    if not isclose(close, price, rel_tol=0, abs_tol=1e-9):
        raise ValueError('Frame close differs from recorded decision price')
    valid_vwap = type(vwap) in (int, float) and isfinite(vwap) and vwap > 0
    return dict(body_bps=(close/opened-1)*10000,
        body_passed=close-opened+1e-9 >= opened*minimum_body_bps/10000,
        minimum_body_bps=minimum_body_bps, execution_vwap=vwap if valid_vwap else None,
        vwap_available=valid_vwap, vwap_passed=valid_vwap and close > vwap,
        price_vs_vwap_pct=(close/vwap-1)*100 if valid_vwap else None)


def run(audit_path, cache, output):
    output.resolve().relative_to(Path('D:/TradingML/runtimes').resolve())
    audit = json.loads(audit_path.read_text())
    if audit['status'] != 'completed':
        raise ValueError('Requires a completed base audit')
    root = Path('D:/TradingML/runtimes/trading/backtest')/audit['run_id']
    summary_path, config_path = root/'run-summary.json', root/'approved-configuration.json'
    summary = json.loads(summary_path.read_text())
    if summary['status'] != 'completed':
        raise ValueError('Replay is not completed')
    config = json.loads(config_path.read_text())
    minimum = config['payload']['strategy']['parameters']['historical_hod']['setup_minimum_body_bps']
    wal = Path(str(cache)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Prepared cache must be closed')
    connection = sqlite3.connect(cache.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    indexed = {r['sequence']:r for r in audit['rows']}
    rows, verified = [], set()
    try:
        for label in audit['support_stop_labels']:
            if not label['label']['valid']:
                continue
            symbol = label['symbol']
            if symbol not in verified:
                raw = connection.execute('select authority_json from strategy_frame_streams where ticker=? and timeframe=?', (symbol, '1s')).fetchone()
                if not raw or json.loads(raw[0]) != summary['data_authority']['sources'][f'derived:{symbol}:1s']:
                    raise ValueError('Prepared frame authority differs from recorded replay')
                verified.add(symbol)
            at = datetime.fromisoformat(label['time'])
            if at.tzinfo is None:
                raise ValueError('Naive decision time')
            raw = connection.execute('select bar_json,indicator_json from strategy_frames where ticker=? and timeframe=? and as_of_us=?', (symbol, '1s', round(at.timestamp()*1e6))).fetchall()
            if len(raw) != 1:
                raise ValueError('Missing or duplicate exact decision frame')
            context = frame_context(json.loads(raw[0][0]), json.loads(raw[0][1]),
                at=at.timestamp(), price=indexed[label['sequence']]['price'], minimum_body_bps=minimum)
            rows.append(dict(label, entry_context=context))
    finally:
        connection.close()
    counts = Counter((r['entry_context']['body_passed'], r['entry_context']['vwap_passed'], r['label']['profitable']) for r in rows)
    save(output, dict(run_id=audit['run_id'], rows=rows, valid_labels=len(rows),
        invalid_labels_retained_in_source=audit['label_counts']['invalid'],
        groups=[dict(body_passed=b, vwap_passed=v, profitable=p, count=n) for (b,v,p),n in sorted(counts.items())],
        inputs={str(p):digest(p) for p in (audit_path, cache, summary_path, config_path, Path(__file__))},
        method='Exact completed 1s frame joins, full source-authority equality, and decision-price equality. Body threshold uses recorded configuration and strategy tolerance. VWAP check uses delivered execution VWAP. These checks do not certify HOD/history, targets, recovery, quote clearance, OMS or later management. Invalid outcome labels remain counted in the source audit. Rows overlap and are not independent trades.'))
    print(f'Joined {len(rows)} valid labels across {len(verified)} tickers: {output}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.audit, args.cache, args.output)
