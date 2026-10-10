"""Versioned searchable catalog, retaining original feature identities."""
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG as BASE,Feature
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS

HISTORY=[]
for family,windows in [('recent_high',range(1,61)),('momentum_close',range(1,61)),
                       ('attention_mean',range(1,61)),('average_move',range(2,33))]:
    for window in windows:
        unit='dollars' if family=='attention_mean' else 'price'
        HISTORY.append(Feature(f'history.{family}.{window}',unit,0.,1e7 if unit=='dollars' else 1e3))
for left in SWING_WINDOWS:
    for right in SWING_WINDOWS:
        HISTORY.append(Feature(f'history.swing_low.{left}.{right}','price',0.,1e3))
# Relative versions avoid absolute-price thresholds while raw values remain accessible.
RELATIVE=tuple(Feature(f'{f.name}.relative_to_close','ratio',-.5,.5) for f in HISTORY if f.unit=='price')
LIQUIDITY=(Feature('market.bid_relative','ratio',-.1,.1),Feature('market.ask_relative','ratio',-.1,.1),
           Feature('market.spread_fraction','ratio',0.,.1),Feature('market.quote_age_seconds','duration',0.,3600.),
           Feature('market.log_execution_volume','log_shares',0.,30.),Feature('market.log_execution_notional','log_dollars',0.,30.),
           Feature('market.log_execution_trade_count','log_count',0.,20.))
CATALOG=(*BASE,*HISTORY,*RELATIVE,*LIQUIDITY)
VERSION='v7-original-plus-causal-history-relative-v1'
PRICE_COLUMNS=tuple(i for i,f in enumerate(HISTORY) if f.unit=='price')
