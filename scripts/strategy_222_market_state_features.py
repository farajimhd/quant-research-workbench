"""Causal market-state features from the replay's certified delivered frames."""
import json
import math

from strategy_222_macd_episodes import analyze_frames, closed_connection, select_observed


def candle_features(bar, previous=None):
    span = bar['high'] - bar['low']
    body = abs(bar['close'] - bar['open'])
    result = dict(green=float(bar['close'] > bar['open']), red=float(bar['close'] < bar['open']))
    if span > 0:
        result.update(body_fraction=body/span,
            upper_wick_fraction=(bar['high']-max(bar['open'],bar['close']))/span,
            lower_wick_fraction=(min(bar['open'],bar['close'])-bar['low'])/span,
            close_location=(bar['close']-bar['low'])/span,
            small_body=float(body/span <= .2))
    if previous:
        result.update(higher_high=float(bar['high']>previous['high']),
            higher_low=float(bar['low']>previous['low']),
            lower_high=float(bar['high']<previous['high']),
            lower_low=float(bar['low']<previous['low']),
            inside_bar=float(bar['high']<=previous['high'] and bar['low']>=previous['low']),
            outside_bar=float(bar['high']>previous['high'] and bar['low']<previous['low']),
            bullish_body_engulfing=float(previous['close']<previous['open'] and bar['close']>bar['open']
                and bar['open']<=previous['close'] and bar['close']>=previous['open']),
            bearish_body_engulfing=float(previous['close']>previous['open'] and bar['close']<bar['open']
                and bar['open']>=previous['close'] and bar['close']<=previous['open']))
    return result


class MarketStates:
    def __init__(self, cache, summary):
        self.streams = {}
        connection = closed_connection(cache)
        try:
            for symbol in summary['tickers']:
                for tf, seconds in (('1s',1),('5s',5)):
                    raw = connection.execute('select authority_json from strategy_frame_streams where ticker=? and timeframe=?', (symbol,tf)).fetchone()
                    if not raw:
                        raise ValueError('Missing canonical stream')
                    authority = json.loads(raw[0])
                    if not authority.get('complete_for_history') or authority != summary['data_authority']['sources'][f'derived:{symbol}:{tf}']:
                        raise ValueError('Canonical stream differs from replay authority')
                    frames = [(us/1e6,json.loads(bar),json.loads(indicator)) for us,bar,indicator in connection.execute(
                        'select as_of_us,bar_json,indicator_json from strategy_frames where ticker=? and timeframe=? order by as_of_us',(symbol,tf))]
                    snapshots,_ = analyze_frames(frames,seconds)
                    previous = None
                    for snapshot,(at,bar,indicator) in zip(snapshots,frames):
                        # A gap has no implied adjacent-candle pattern.
                        adjacent = previous if snapshot['gap_since_previous_seconds']==0 else None
                        geometry = candle_features(bar,adjacent)
                        for name in ('atr_14','price_change_1_bar_pct','price_vs_execution_vwap_pct'):
                            value = indicator.get(name)
                            if type(value) in (int,float) and math.isfinite(value):
                                geometry['atr_pct' if name=='atr_14' else name] = value/bar['close']*100 if name=='atr_14' else value
                        snapshot['geometry'] = geometry
                        previous = bar
                    self.streams[(symbol,tf)] = snapshots,[r['as_of'] for r in snapshots]
        finally:
            connection.close()

    def at(self, symbol, decision, at):
        macd = (decision.get('metadata') or {}).get('macd') or {}
        delivered_one = at if macd.get('observed_at')==at and any(
            str(s).startswith(f'qmd-derived:{symbol}:1s:') for s in decision.get('source_signal_ids',[])) else None
        output = {}; evidence = {}
        for tf,cutoff in (('1s',delivered_one),('5s',macd.get('completed_base_at'))):
            value = select_observed(*self.streams[(symbol,tf)],cutoff,at)
            evidence[tf] = dict(status=value['status'],delivered_cutoff=cutoff)
            if value['status']!='measured':
                continue
            for name in ('positive','histogram_bps','histogram_slope_bps_per_second','episode_age_seconds',
                         'observed_candles','left_censored','max_gap_seconds','histogram_fraction_of_peak',
                         'pullback_pct','progress_pct','source_age_seconds'):
                v = value.get(name)
                if isinstance(v,(int,float)) and math.isfinite(v):
                    output[f'completed_{tf}.{name}'] = float(v)
            for name,v in value['geometry'].items():
                output[f'candle_{tf}.{name}'] = v
            atr = value['geometry'].get('atr_pct')
            if atr is not None and atr > 0:
                # Separate episode shape from a ticker's absolute volatility.
                for name in ('histogram_bps','histogram_slope_bps_per_second'):
                    if isinstance(value.get(name),(int,float)):
                        output[f'completed_{tf}.{name}_per_atr_bps'] = value[name]/(atr*100)
                for name in ('price_change_1_bar_pct','price_vs_execution_vwap_pct'):
                    if name in value['geometry']:
                        output[f'candle_{tf}.{name}_per_atr_pct'] = value['geometry'][name]/atr
        a,b = output.get('completed_1s.positive'),output.get('completed_5s.positive')
        if a is not None and b is not None:
            output['macd_completed_timeframes_agree'] = float(a==b)
            output['macd_completed_both_positive'] = float(a==1 and b==1)
        return output,evidence
