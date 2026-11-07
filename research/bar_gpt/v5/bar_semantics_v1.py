"""Shared, versioned semantics for transient bars used by pretraining and later phases.

The input authority is canonical SIP events, ordered by (sip_timestamp_us, ordinal).
Predicates use ARTE's sale-condition mapping and structural quote-pair rules.
Reporting-clock status is diagnostic only for encoder pretraining. Sale-condition
rules independently govern last price, extremes and volume, as in ARTE.
All timeframes use the same eligibility. Daily means 09:30–16:00 New York.
No function in this module writes bars, shards, tables, or model artifacts.
"""

from pipelines.market_sip.events.market_day_sql import condition_expressions


CONTRACT = dict(
    version="bargpt-shared-bar-semantics-v3-trade-driven",
    ordering="sip_timestamp_us,ordinal",
    trade_reporting="no reporting-clock filter; include all structurally valid trades under ARTE per-field sale-condition rules",
    trade_conditions="ARTE independent update_last/update_high_low/update_volume with extended-hours Form T",
    quotes="positive noncrossed pair; finite nonnegative sizes; latest invalid quote clears carry",
    quote_carry="causal within 04:00-20:00 session; no age cutoff; actual quote counts remain unchanged",
    daily="09:30-16:00 America/New_York; same eligibility as intraday",
    samples="complete trade OHLC and positive eligible volume/count define observations; quotes/status never create bars; fine frames stay in session with masked right padding; hourly/daily span sessions with split adjustment",
)


def predicates(rules):
    """Return SQL eligibility aliases for canonical decoded event columns.

    rules is the frozen list of canonical trade-condition dictionary records.
    The returned SQL expects kind, event_meta, price_int, secondary_int,
    size_primary, size_secondary, tokens and local_second in the same scope.
    """
    form, last, high, volume = condition_expressions(
        [row for row in rules if row["source_family"] == "trade_conditions"]
    )
    return f"""({form}) form_ok,
        kind=1 AND price_int>0
          AND size_primary>0 AND isFinite(size_primary) usable,
        usable AND {last} p_ok, usable AND {high} e_ok, usable AND {volume} v_ok,
        kind=0 AND secondary_int>0 AND price_int>=secondary_int
          AND isFinite(size_primary) AND isFinite(size_secondary)
          AND size_primary>=0 AND size_secondary>=0 q_ok"""


def family_statistics():
    """Return base/rollup SQL plus ordered value/mask names for 18 market channels.

    Aggregates consume events; reducers consume 100ms sufficient statistics.
    Open/close use event order, extremes use eligible observations, trade volume
    sums sizes, and quote size is the last observed displayed size. Quote carry
    is applied only after aggregation, so it never inflates event counts.
    """
    aggregates, reducers, values, validity = [], [], [], []
    for side, price, size, p, e, a in (
        ("trade", "price_int", "size_primary", "p_ok", "e_ok", "v_ok"),
        ("bid", "secondary_int", "size_secondary", "q_ok", "q_ok", "q_ok"),
        ("ask", "price_int", "size_primary", "q_ok", "q_ok", "q_ok"),
    ):
        names = [f"{side}_{field}" for field in ("o", "h", "l", "c", "s", "n")]
        masks = [f"{side}_{field}" for field in ("pv", "ev", "av")]
        aggregates.extend([
            f"argMinIf({price},tuple(sip_timestamp_us,ordinal),{p})/10000. {names[0]}",
            f"maxIf({price},{e})/10000. {names[1]}",
            f"minIf({price},{e})/10000. {names[2]}",
            f"argMaxIf({price},tuple(sip_timestamp_us,ordinal),{p})/10000. {names[3]}",
            (f"sumIf(toFloat64({size}),{a})" if side == "trade" else
             f"argMaxIf(toFloat64({size}),tuple(sip_timestamp_us,ordinal),{a})") + f" {names[4]}",
            f"countIf({a}) {names[5]}",
            f"countIf({p})>0 {masks[0]}", f"countIf({e})>0 {masks[1]}",
            f"countIf({a})>0 {masks[2]}",
        ])
        reducers.extend([
            f"argMinIf({names[0]},b,{masks[0]}) {names[0]}",
            f"maxIf({names[1]},{masks[1]}) {names[1]}",
            f"minIf({names[2]},{masks[1]}) {names[2]}",
            f"argMaxIf({names[3]},b,{masks[0]}) {names[3]}",
            (f"sum({names[4]})" if side == "trade" else f"argMaxIf({names[4]},b,{masks[2]})") + f" {names[4]}",
            f"sum({names[5]}) {names[5]}",
            *(f"max({mask}) {mask}" for mask in masks),
        ])
        values.extend(names)
        validity.extend([masks[0], masks[1], masks[1], masks[0],
                         "1" if side == "trade" else masks[2], "1"])
    return aggregates, reducers, values, validity


def quote_carry_projection(value_names, mask_names):
    """Project 18 market channels with causal quote fallback after aggregation.

    qb.book must be the latest actual quote message strictly before bar end,
    including invalid messages: tuple(valid,bid,ask,bid_size,ask_size).
    OHLC extrema remain observed when updates exist. A final invalidation masks
    close/size; a bar without updates receives the carried price only if valid.
    """
    values, masks = list(value_names), list(mask_names)
    for offset, side, price_field, size_field in ((6, "bid", 2, 4), (12, "ask", 3, 5)):
        carried_price = f"if(qb.book.1,qb.book.{price_field}/10000.,0.)"
        for position, field in enumerate(("o", "h", "l")):
            values[offset+position] = f"if({side}_n>0,{side}_{field},{carried_price})"
            masks[offset+position] = f"({side}_pv OR qb.book.1)"
        values[offset+3] = carried_price
        values[offset+4] = f"if(qb.book.1,qb.book.{size_field},0.)"
        masks[offset+3] = masks[offset+4] = "qb.book.1"
    return values, masks
