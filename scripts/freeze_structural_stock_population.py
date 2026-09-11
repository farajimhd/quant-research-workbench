"""Freeze a common-stock successor before inspecting any strategy outcomes."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from urllib.parse import urlencode, urlparse, parse_qsl
from urllib.request import Request, urlopen

from scripts.audit_structural_baseline import write
from scripts.freeze_structural_validation_population import SELECTION_DATE, SESSIONS, SEED
from scripts.repair_qmd_live_canonical_bars import load_dotenv
from scripts.swing_book_paths import validate_runtime_root


def safe_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.massive.com' or parsed.path != '/v3/reference/tickers':
        raise ValueError('Unexpected reference pagination destination')
    params = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() != 'apikey']
    return 'https://api.massive.com/v3/reference/tickers?' + urlencode(params)


def select_stocks(population, references):
    lookup = {}
    for row in references:
        ticker = row['ticker']
        if ticker in lookup:
            raise ValueError('Duplicate historical reference ticker')
        lookup[ticker] = row
    selected = []
    reasons = Counter()
    decisions = []
    for row in population['ranked_eligible']:
        ticker = row['source_ticker']
        ref = lookup.get(ticker)
        if not ref:
            reason = 'historical_reference_missing'
        elif ref.get('market') != 'stocks' or ref.get('locale') != 'us':
            reason = 'outside_us_stock_market'
        elif not ref.get('type'):
            reason = 'historical_type_missing'
        elif ref['type'] != 'CS':
            reason = 'not_common_stock'
        else:
            reason = 'eligible_common_stock'
            selected.append(ticker)
        reasons[reason] += 1
        decisions.append(dict(ticker=ticker, reason=reason, instrument_type=ref.get('type') if ref else None))
    if len(selected) < 10:
        raise ValueError('Insufficient common-stock population')
    # Missing classifications are not permission to advance to a more convenient ticker.
    last_selected = next(i for i, r in enumerate(decisions) if r['ticker'] == selected[9])
    if any(d['reason'] in ('historical_reference_missing', 'historical_type_missing') for d in decisions[:last_selected + 1]):
        raise ValueError('Unknown classification before sample cutoff; resolve before freezing')
    return dict(development=selected[:5], sealed_holdout=selected[5:10],
                eligible_common_stocks=len(selected), counts=dict(reasons), decisions=decisions)


def acquire(root):
    url = 'https://api.massive.com/v3/reference/tickers?' + urlencode(dict(date=SELECTION_DATE, market='stocks', locale='us', active='true', limit=1000, sort='ticker', order='asc'))
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    token = os.getenv('MASSIVE_API_KEY') or os.getenv('POLYGON_API_KEY')
    if not token:
        raise ValueError('Existing vendor credential unavailable')
    rows = []
    pages = []
    visited = set()
    for index in range(30):
        url = safe_url(url)
        if url in visited:
            raise ValueError('Reference pagination loop')
        visited.add(url)
        path = root / f'page-{index:03d}.json'
        if path.exists():
            saved = json.loads(path.read_text())
            if saved['url'] != url:
                raise ValueError('Cached reference page identity changed')
            payload = saved['response']
        else:
            request = Request(url, headers={'Authorization': f'Bearer {token}'})
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get('status') not in ('OK', 'DELAYED') or not isinstance(payload.get('results'), list):
                raise ValueError('Invalid historical ticker reference response')
            # Never persist an API key returned inside a pagination URL.
            if payload.get('next_url'):
                payload['next_url'] = safe_url(payload['next_url'])
            write(path, dict(url=url, received_at=datetime.now(timezone.utc).isoformat(), response=payload))
        rows.extend(payload['results'])
        if len(rows) > 30000:
            raise ValueError('Historical population bound exceeded')
        pages.append(dict(path=path.name, sha256=sha256(path.read_bytes()).hexdigest()))
        print(f'Historical classification: active=1 pages={index+1} tickers={len(rows)}', flush=True)
        url = payload.get('next_url')
        if not url:
            return rows, pages
    raise ValueError('Historical reference page bound exceeded; no partial selection')


def main(root, parent):
    root = validate_runtime_root(root)
    root.mkdir(parents=True, exist_ok=True)
    population_path = parent / 'population.json'
    plan = dict(version=1, selection_date=SELECTION_DATE, seed=SEED, sessions=SESSIONS,
                parent_sha256=sha256(population_path.read_bytes()).hexdigest(),
                source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                filter='US stocks, historical active=true, type=CS; retain original hash ranking',
                reason='Exclude funds and non-common-stock instruments before reading strategy outcomes.',
                limitation='Vendor historical reference reconstructed today; not a contemporaneously archived reference. No market-cap screen.',
                documentation='https://massive.com/docs/rest/stocks/tickers')
    path = root / 'plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise ValueError('Frozen plan differs; use a successor runtime')
    write(path, plan)
    rows, pages = acquire(root)
    result = select_stocks(json.loads(population_path.read_text()), rows)
    result['reference_pages'] = pages
    path = root / 'population.json'
    if path.exists() and json.loads(path.read_text()) != result:
        raise ValueError('Frozen population changed')
    write(path, result)
    print(f'Classification complete: active=0 queued=0 failed=0; {result["counts"]}; {path}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--parent', type=Path, required=True)
    args = parser.parse_args()
    main(args.runtime, args.parent)
