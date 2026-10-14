"""Opening-known split metadata for ranking before feature construction."""
import json
from research.rl_trading.v1 import reference_features
from research.rl_trading.v1.arte_sql import literal,query
from research.rl_trading.v1.common import digest
from research.rl_trading.v6.universe_scope import scope_population
from research.vectorized_backtest.v6.torch_backtest.splits import price_factor


def opening_basis(reader,members,day,previous):
    """Use the same static scope and opening cutoff as feature certification."""
    from research.vectorized_backtest.v6.torch_backtest.encoding.config import DEFAULT_EXCLUDED_TICKERS
    selected,scope=scope_population(reader,[m for m in members if m['ticker'] not in DEFAULT_EXCLUDED_TICKERS])
    storage=reference_features.storage_check(reader)
    cutoff="toDateTime64("+literal(reference_features.opening(day))+",9,'UTC')"
    actions=query(reader,
        'SELECT symbol_id,listing_id,security_id,execution_date,split_from,split_to,inserted_at,source_content_sha256 '
        'FROM q_live.market_stock_split_v1 FINAL '
        f'WHERE execution_date<=toDate({literal(day)}) AND inserted_at<={cutoff} '
        'ORDER BY listing_id,execution_date,inserted_at')
    grouped={}
    for row in actions:
        grouped.setdefault(tuple(row[k] for k in ('symbol_id','listing_id','security_id')),[]).append(row)
    records={}
    for member in selected:
        rows=grouped.get(tuple(member[k] for k in ('symbol_id','listing_id','security_id')),[])
        records[member['listing_id']]=dict(ticker=member['ticker'],splits=rows,
            rvol_price_factor=price_factor(rows,str(previous),str(day)))
    result=json.loads(json.dumps(dict(version='v8-ranking-opening-basis-v1',day=str(day),
        previous_day=str(previous),opening_asof_utc=reference_features.opening(day),
        universe_scope=scope,storage=storage,listings=records),default=str))
    result['hash']=digest(result)
    return result


def selected_identities(top,members):
    """Union for computation only; runtime eligibility remains per-clock top-N."""
    import numpy as np
    top=np.asarray(top)
    if top.ndim!=2 or top.dtype.kind not in 'iu' or np.any(top < -1) or np.any(top>=len(members)):
        raise ValueError('Invalid causal ranking indices')
    return [members[int(i)]['listing_id'] for i in np.unique(top[top>=0])]


def feature_scope(market_root,days,sessions_sha256):
    """Bind each day's own selected identities and the next day's warm-up set."""
    from pathlib import Path
    import numpy as np
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    if not days or days!=sorted(set(days)):raise ValueError('Ordered unique ranking days required')
    root=Path(market_root);selected={};receipts={}
    for day in days:
        path=root/day/'complete.json';record=json.loads(path.read_text(encoding='utf-8-sig'))
        if (record.get('status')!='complete' or record.get('split_role')!='validation' or record.get('validation_opened') is not True
                or record['identity']['sessions_sha256']!=sessions_sha256
                or not record['identity'].get('ranking_before_features') or record['day']!=day):
            raise ValueError('Ranking-first source contract mismatch')
        for name,value in record['files'].items():
            if file_hash(path.parent/name)!=value:raise ValueError('Ranking source changed')
        members=json.loads((path.parent/'eligibility.json').read_text(encoding='utf-8-sig'))['listings']
        selected[day]=selected_identities(np.load(path.parent/'top_indices.npy',allow_pickle=False),members)
        receipts[day]=file_hash(path)
    needed={day:sorted(set(selected[day])|set(selected[days[i+1]] if i+1<len(days) else []))
            for i,day in enumerate(days)}
    result=dict(version='v8-ranked-feature-scope-v1',sessions_sha256=sessions_sha256,
        market_root=str(root),market_receipts=receipts,selected=selected,needed=needed)
    result['hash']=digest(result)
    return result
