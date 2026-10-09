"""Bounded historical quote verification from the exact certified producer."""
import json


def load_structural_rejection_exit_quote(client,witness,*,profile):
    """Cold read only; never a query in the sequential decision/fill loop."""
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from .profit_armed_structural_rejection import StructuralRejectionWitness,CurrentQuote
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS,_literal,assert_select_only
    require_native_structural_rejection_profile(profile)
    owner=profile.owner
    if type(witness) is not StructuralRejectionWitness:
        raise ValueError('Structural rejection quote requires exact replayed own witness')
    source=witness.predecessor.source
    units=tuple(unit for unit in owner.market.units
        if (unit.session_date,unit.ticker,unit.stage)==(source.session_date,source.ticker,'broker_100ms'))
    if (owner.lookup.sources.get(source.ticker)!=source or witness.policy!=owner.declaration.policy
            or source.market_build_id!=owner.market.build_id or source.market_plan_token!=owner.market.token
            or len(units)!=1 or units[0].attempt_id!=source.liquidity_attempt_id
            or type(witness.decision_boundary_ms) is not int or witness.decision_boundary_ms%100):
        raise ValueError('Structural rejection quote differs from exact certified source scope')
    bucket=(witness.decision_boundary_ms+SESSION_OPEN_OFFSET_MS)//100-1
    sql=assert_select_only('SELECT build_id,session_date,ticker,attempt_id,bucket_index,'
        'bid_int,ask_int,quote_valid,quote_timestamp_us FROM arte.liquidity_100ms_v1 '
        f'WHERE build_id={_literal(source.market_build_id)} '
        f'AND session_date=toDate({_literal(source.session_date)}) '
        f'AND ticker={_literal(source.ticker)} '
        f'AND attempt_id=toUUID({_literal(source.liquidity_attempt_id)}) '
        f'AND bucket_index={bucket} LIMIT 2 '
        'SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')
    response=client.execute(sql)
    if type(response) is not str or len(response)>262144 or len(response.encode('utf-8'))>262144:
        raise ValueError('Structural rejection quote response exceeds bounded text bytes')
    rows=tuple(json.loads(line) for line in response.splitlines() if line.strip())
    if len(rows)!=1:
        raise ValueError('Structural rejection quote is missing or ambiguous')
    row=rows[0]
    if (type(row) is not dict or set(row)!={'build_id','session_date','ticker','attempt_id','bucket_index',
            'bid_int','ask_int','quote_valid','quote_timestamp_us'}
            or (row['build_id'],row['session_date'],row['ticker'],row['attempt_id'])!=(
                source.market_build_id,source.session_date,source.ticker,source.liquidity_attempt_id)
            or any(type(row[name]) is not int for name in (
                'bucket_index','bid_int','ask_int','quote_valid','quote_timestamp_us'))
            or row['bucket_index']!=bucket or row['quote_valid']!=1):
        raise ValueError('Structural rejection quote differs from actual producer identity or clock')
    actual=CurrentQuote(source,row['quote_timestamp_us'],row['bid_int'],row['ask_int'])
    if actual!=witness.quote:
        raise ValueError('Structural rejection quote differs from exact saved exchange timestamp/prices')
    return actual
