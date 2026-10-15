"""Bounded source-model trace using the unchanged V19 transition oracle.

This diagnostic consumes prepared optimizer tensors only. It is never an app
market source, OMS, broker, or alternative feature producer.
"""
from datetime import datetime, timezone
import numpy as np
import polars as pl
import torch

from .evaluator import PopulationPrograms, state_signals, step, replay_cohort
from .genome import STAGES
from .swing_projection import entry_swings

POSITION_FIELDS = ('quantity', 'average_price', 'stop_price', 'target_price',
                   'high_water_price', 'adds', 'last_management_clock',
                   'net_realized_pnl', 'episode_start_pnl')


def trace_reference_session(data, member, execution, *, max_rows, gates=None,
                            maximum_gate_gib=1., verify_replay=True):
    """Trace every listing/clock from the same source step used by replay_cohort.

    Gates may be supplied from the campaign's prepared source evaluator. The
    trace preserves flat/open packed gates and age-selected gates separately.
    Quantity changes are source model transitions, never fabricated broker fills.
    """
    member.validate()
    execution.validate()
    u, t = len(data.listing_ids), data.clocks
    if type(max_rows) is not int or max_rows < 1 or (t - 1) * u > max_rows or t < 2:
        raise ValueError('Reference trace exceeds explicit row bound')
    clocks = np.asarray(data.arrays['clocks'])
    if clocks.dtype != np.dtype('int64') or len(clocks) != t or not np.all(np.diff(clocks) == 1):
        raise ValueError('Reference trace requires exact dense source clocks')
    listing_ids = []
    for index in data.listing_ids:
        listing_ids.append(data.receipt['listings'][int(index)]['listing_id'])
    if len(set(listing_ids)) != u:
        raise ValueError('Reference listing identity is duplicated')
    if gates is None:
        plan = PopulationPrograms([member], data.device)
        try:
            gates = plan.evaluate(data, maximum_gate_gib=maximum_gate_gib)
        finally:
            plan.close()
    if gates.shape != (1, t, u) or gates.dtype not in (torch.uint8, torch.int16) or gates.device != data.device:
        raise ValueError('Reference gates differ from prepared session shape/device')
    device = data.device
    position = torch.zeros((1, 1, u, 9), dtype=torch.float64, device=device)
    aggregate = torch.zeros((1, 1, 16 if execution.risk_protection else 12), dtype=torch.float64, device=device)
    aggregate[..., 8] = float('inf')
    opened = torch.zeros((1, 1, u), dtype=torch.float64, device=device)
    policy = torch.tensor([[member.policy.stop_fraction, member.policy.target_fraction,
        member.policy.reduce_fraction, member.policy.trail_fraction, member.policy.add_minimum_profit,
        member.policy.reduce_minimum_profit, member.policy.maximum_adds, member.policy.cooldown,
        member.policy.swing_left]], dtype=torch.float64, device=device)[None, :, None, :]
    limits = torch.tensor([[member.minimum_age.get(stage, 0) for stage in STAGES]],
        dtype=torch.float64, device=device)[None, :, None, :]
    values, columns, history_ids = entry_swings(data, [member])
    records = []
    for clock in range(1, t):
        before = position
        held = before[..., 0] > 1e-12
        raw = gates[:, clock - 1][None]
        selected = state_signals(raw, held, clock - 1 - opened, limits, gates.dtype == torch.int16)
        swing = values[history_ids[clock, None, None, :], columns[None, :, None]]
        price = data.tensors['mark'][clock][None, None]
        observed = data.tensors['observed'][clock][None, None]
        membership = data.tensors['membership'][clock - 1][None, None]
        position, aggregate = step(before, aggregate, price,
            data.tensors['mark'][clock - 1][None, None], observed, membership, selected,
            swing, policy, torch.tensor(float(clock), device=device),
            torch.tensor(clock == t - 1, device=device)[None, None, None],
            execution.entry_dollars, execution.add_dollars, execution.cost_bps / 10000.,
            execution.fee_per_share, execution.minimum_order_fee, execution.risk_protection,
            execution.maximum_stop_distance, execution.maximum_position_risk)
        opened = torch.where(~held & (position[..., 0] > 1e-12), float(clock), opened)
        # Bulk transfer per clock; no per-cell tensor synchronization.
        records.append((before[0, 0].detach().cpu().numpy(), position[0, 0].detach().cpu().numpy(),
            raw[0, 0].detach().cpu().numpy(), selected[0, 0].detach().cpu().numpy(),
            price[0, 0].detach().cpu().numpy(), observed[0, 0].cpu().numpy(), membership[0, 0].cpu().numpy()))
    columns_out = {'listing_id': listing_ids * (t - 1),
        'signal_at': [datetime.fromtimestamp(int(at), timezone.utc) for at in np.repeat(clocks[:-1], u)],
        'execution_at': [datetime.fromtimestamp(int(at), timezone.utc) for at in np.repeat(clocks[1:], u)]}
    before = np.concatenate([row[0] for row in records])
    after = np.concatenate([row[1] for row in records])
    for i, name in enumerate(POSITION_FIELDS):
        columns_out['before_' + name] = before[:, i]
        columns_out['after_' + name] = after[:, i]
    for i, name in ((2, 'raw_gate_bits'), (3, 'selected_gate_bits'),
                    (4, 'source_execution_mark'), (5, 'observed'), (6, 'membership')):
        columns_out[name] = np.concatenate([row[i] for row in records])
    frame = pl.DataFrame(columns_out).sort('signal_at', 'listing_id').with_columns(
        (pl.col('after_quantity') - pl.col('before_quantity')).alias('quantity_change'))
    frame = frame.with_columns(pl.when(pl.col('quantity_change') > 0)
        .then(pl.when(pl.col('before_quantity') <= 1e-12).then(pl.lit('entry')).otherwise(pl.lit('add')))
        .when(pl.col('quantity_change') < 0)
        .then(pl.when(pl.col('after_quantity') <= 1e-12).then(pl.lit('exit')).otherwise(pl.lit('reduce')))
        .otherwise(pl.lit('hold')).alias('transition'))
    # Step models one immediate buy/sell per listing. Cost attribution uses its
    # source arithmetic; these are assumptions, not broker price/fee evidence.
    frame = frame.with_columns(pl.when(pl.col('transition') == 'entry').then(execution.entry_dollars)
        .when(pl.col('transition') == 'add').then(execution.add_dollars).otherwise(0.).alias('source_buy_dollars'),
        (-pl.col('quantity_change')).clip(lower_bound=0).alias('source_sold_quantity'))
    frame = frame.with_columns((pl.col('source_buy_dollars') / pl.col('source_execution_mark')).fill_nan(0.).alias('source_bought_quantity'))
    frame = frame.with_columns((
        pl.when(pl.col('source_bought_quantity') > 0)
        .then(pl.max_horizontal(pl.col('source_bought_quantity') * execution.fee_per_share, pl.lit(execution.minimum_order_fee))).otherwise(0.)
        + pl.when(pl.col('source_sold_quantity') > 0)
        .then(pl.max_horizontal(pl.col('source_sold_quantity') * execution.fee_per_share, pl.lit(execution.minimum_order_fee))).otherwise(0.)
        ).alias('modeled_commission'),
        ((pl.col('source_buy_dollars') + pl.col('source_sold_quantity') * pl.col('source_execution_mark'))
            * (execution.cost_bps / 10000.)).fill_nan(0.).alias('modeled_notional_cost'))
    # Source packed gates conflate false and invalid. Independent validity is
    # unavailable from these retained gates and must remain unknown.
    frame = frame.with_columns(pl.lit('not_recovered').alias('raw_gate_validity'))
    frame = frame.with_columns(*[
        (((pl.col('raw_gate_bits') // (1 << (index + (8 if gates.dtype == torch.int16 and stage != 'entry' else 0)))) % 2) == 1).alias(stage)
        for index, stage in enumerate(STAGES)])
    tables = dict(
        rule_outputs=frame.select('listing_id', 'signal_at', 'raw_gate_bits', 'raw_gate_validity', *STAGES),
        actions=frame.select('listing_id', 'signal_at', 'execution_at', 'selected_gate_bits', 'transition', 'quantity_change'),
        positions=frame.select('listing_id', 'signal_at', 'execution_at',
            *['before_' + name for name in POSITION_FIELDS], *['after_' + name for name in POSITION_FIELDS]),
        fees_pnl=frame.select('listing_id', 'execution_at', 'source_execution_mark',
            'modeled_commission', 'modeled_notional_cost', 'after_net_realized_pnl'),
        membership=frame.select('listing_id', 'signal_at', 'execution_at', 'membership', 'observed'))
    metrics = None
    if verify_replay:
        # This independent original replay also exports exact source episodes.
        metrics = replay_cohort([data], [member], [gates], execution=execution,
            backend='eager', trace=True)[0]
        actual = position[0, 0, :, 7].sum()
        if not torch.equal(actual, metrics['net_pnl'][0]):
            raise ValueError('Reference trace differs from original replay oracle')
    return dict(model='v19-assumed-immediate-fill', complete=True, trace=frame, tables=tables,
        metrics=metrics, unavailable=dict(orders='optimizer has no OMS order model',
            fills='optimizer has no broker execution identities or observed fills'),
        qualification='source-model reference; no broker causality established')
