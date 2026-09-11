"""Freeze low-priced SIP ticker selection before reading evaluation outcomes."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

from scripts.audit_structural_baseline import write
from scripts.repair_qmd_live_canonical_bars import load_dotenv, connection_from_env
from scripts.swing_book_paths import validate_runtime_root

SEED = 'structural-unseen-tickers-20260911-v1'
SELECTION_DATE = '2026-07-31'
SESSIONS = ['2026-08-24', '2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28']
EXCLUDED = {'SUGP', 'JUNS', 'AAPL'}


def select(rows):
    """Selection is independent of book availability and subsequent returns."""
    cutoff = int(datetime(2026, 8, 24, 4, tzinfo=ZoneInfo('America/New_York')).timestamp() * 1e6)
    seen = set()
    eligible = []
    for row in rows:
        ticker = row['source_ticker']
        if ticker in seen:
            raise ValueError(f'Duplicate selection ticker: {ticker}')
        seen.add(ticker)
        if row['source_contract'] != 'ordered_sip_events_unadjusted' or row['adjusted'] != 0:
            raise ValueError('Unexpected selection authority')
        if row['session_date'] != SELECTION_DATE or row['session_kind'] != 'regular':
            raise ValueError('Selection row outside frozen session')
        if row['available_at_us'] >= cutoff:
            raise ValueError('Selection information was unavailable before evaluation')
        values = [row[k] for k in ('trade_close', 'trade_size_sum', 'trade_price_size_sum')]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite selection values')
        if ticker not in EXCLUDED and row['trade_present'] and 1 <= row['trade_close'] <= 20 and row['trade_size_sum'] >= 100000 and row['trade_price_size_sum'] >= 1e6:
            eligible.append(row)
    ranked = sorted(eligible, key=lambda r: sha256(f'{SEED}:{r["source_ticker"]}'.encode()).hexdigest())
    if len(ranked) < 10:
        raise ValueError('Insufficient eligible population; do not weaken selection')
    return dict(population_count=len(rows), eligible_count=len(ranked),
                development=[r['source_ticker'] for r in ranked[:5]],
                sealed_holdout=[r['source_ticker'] for r in ranked[5:10]],
                ranked_eligible=ranked)


def main(root):
    root = validate_runtime_root(root)
    root.mkdir(parents=True, exist_ok=True)
    fields = 'source_ticker,session_date,session_kind,source_contract,adjusted,available_at_us,trade_present,trade_close,trade_size_sum,trade_price_size_sum'
    sql = f"SELECT {fields} FROM daily_session_bars_by_symbol_time_v1 FINAL WHERE session_date='{SELECTION_DATE}' AND session_kind='regular' ORDER BY source_ticker LIMIT 20001 SETTINGS max_threads=2,max_execution_time=30 FORMAT JSONEachRow"
    plan = dict(version=1, seed=SEED, selection_date=SELECTION_DATE, sessions=SESSIONS,
                query=sql, excluded=sorted(EXCLUDED), source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Low-priced SIP tickers eligible on July 31; not a market-cap or common-stock screen.',
                missing_data='Retain selected units; no replacement based on book availability or outcomes.',
                release='Holdout remains sealed until a rule set is frozen; original release criteria remain binding.')
    plan_path = root / 'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError('Frozen selection differs; use a successor runtime')
    write(plan_path, plan)
    input_path = root / 'selection-inputs.json'
    if input_path.exists():
        rows = json.loads(input_path.read_text())
    else:
        print('Selection: active=1 queued=0; reading pre-period canonical metadata', flush=True)
        load_dotenv(Path(__file__).resolve().parents[1] / '.env')
        rows = connection_from_env('market_sip_compact').json_rows(sql, timeout=40)
        if len(rows) >= 20001:
            raise ValueError('Selection population exceeds bound; no truncated sample')
        write(input_path, rows)
    result = select(rows)
    result['input_sha256'] = sha256(input_path.read_bytes()).hexdigest()
    result_path = root / 'population.json'
    if result_path.exists() and json.loads(result_path.read_text()) != result:
        raise ValueError('Frozen selection inputs or result changed')
    write(result_path, result)
    print(f'Selection: completed=1 failed=0 active=0 queued=0; eligible={result["eligible_count"]}; development=5 holdout=5; {result_path}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    main(parser.parse_args().runtime)
