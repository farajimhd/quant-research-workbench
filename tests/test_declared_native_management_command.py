"""Replay actual prepared manager output; no installed/durable admission claim."""
import asyncio
from dataclasses import replace

import pytest

from test_backtest_declared_native_fixed_management import manager, parent, rows, held
from src.trading_runtime.declared_native_management_command import (
    DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand,
)
from src.trading_runtime.strategy_one_position import ResistanceBreak


async def exit_command(bundle):
    m, runtime, evidence, _, p, _ = bundle
    view = await held(bundle)
    ev, frame = rows(m, p, 40000, bid=p.initial_stop, line=.01, signal=.02)
    evidence.rows[40000] = ev
    await m.on_management(view, frame, 40000)
    command = runtime.commands[-1]
    assert type(command) is DeclaredExitCommand
    assert command.replay() == ('zero_regime', command.witness)
    return command


@pytest.mark.parametrize('mutation', (
    'priority', 'witness', 'witness-clock-alias', 'future5s', 'future10s',
    'clock-alias', 'close-alias', 'age-alias', 'original-risk', 'financial',
    'producer', 'first-held', 'pending', 'future-candle', 'identity-alias',
))
def test_actual_exit_receiver_rejects_altered_producer_or_claim(manager, mutation):
    command = asyncio.run(exit_command(manager))
    with pytest.raises(ValueError):
        if mutation == 'priority':
            changed = replace(command, kind='early_original_risk')
        elif mutation == 'witness':
            changed = replace(command, witness=replace(command.witness, bid=command.witness.bid+.01))
        elif mutation == 'witness-clock-alias':
            changed = replace(command, witness=replace(command.witness, boundary_ms=float(command.witness.boundary_ms)))
        elif mutation in ('future5s', 'clock-alias', 'close-alias', 'age-alias', 'original-risk', 'pending'):
            key, value = {
                'future5s': ('completed_five_second_boundary_ms', 45000),
                'clock-alias': ('boundary_ms', 40000.),
                'close-alias': ('completed_five_second_close_int', True),
                'age-alias': ('quote_age_us', False),
                'original-risk': ('reference_ask', command.context.source.reference_ask+.01),
                'pending': ('pending_exit', True),
            }[mutation]
            changed = replace(command, inputs=replace(command.inputs,
                completed=replace(command.inputs.completed, **{key: value})))
        elif mutation == 'future10s':
            changed = replace(command, inputs=replace(command.inputs, ten={'boundary_ms': 50000}))
        elif mutation == 'future-candle':
            from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeCandle
            changed = replace(command, inputs=replace(command.inputs, candles=(LiquidityFadeCandle(45000, 1),)))
        elif mutation == 'identity-alias':
            changed = replace(command, context=replace(command.context,
                source=replace(command.context.source, revision=float(command.context.source.revision))))
        elif mutation == 'financial':
            changed = replace(command, context=replace(command.context,
                financial=replace(command.context.financial, ticker='foreign')))
        elif mutation == 'producer':
            refs = dict(command.context.observation_source)
            refs['source_liquidity_attempt_id'] = '33333333-3333-4333-8333-333333333333'
            changed = replace(command, context=replace(command.context, observation_source=refs))
        else:
            changed = replace(command, context=replace(command.context, first_held_boundary_ms=40000))
        changed.replay()


def test_exit_inputs_missing_product_is_no_synthetic_exit_and_nested_copies_are_frozen(manager):
    command = asyncio.run(exit_command(manager))
    ten = {'boundary_ms': 40000, 'nested': {'value': [1]}}
    inputs = replace(command.inputs, ten=ten)
    ten['nested']['value'].append(2)
    assert inputs.ten['nested']['value'] == (1,)
    with pytest.raises(TypeError):
        inputs.ten['boundary_ms'] = 50000
    missing = replace(command.inputs,
        completed=replace(command.inputs.completed, macd_signal=None))
    assert missing.replay(command.context) is None
    with pytest.raises(ValueError):
        replace(command, inputs=missing).replay()


async def protection_command(bundle):
    m, runtime, evidence, _, p, _ = bundle
    view = await held(bundle)
    breaks = tuple(ResistanceBreak(40000, dict(unified_level_id=f'R{i}',
        lower=p.reference_ask+i*.02, upper=p.reference_ask+i*.02+.001,
        role='resistance', side='resistance')) for i in (1, 2, 3))
    ev, frame = rows(m, p, 40000, bid=p.reference_ask+.2, breaks=breaks)
    evidence.rows[40000] = ev
    await m.on_management(view, frame, 40000)
    command = runtime.commands[-1]
    assert type(command) is DeclaredProtectionCommand
    assert command.replay().stop_amendment is not None
    return command


@pytest.mark.parametrize('mutation', ('quote', 'future-low', 'future-break', 'prior', 'claim', 'priority'))
def test_actual_protection_receiver_replays_full_reducer_and_priority(manager, mutation):
    command = asyncio.run(protection_command(manager))
    with pytest.raises(ValueError):
        if mutation == 'quote':
            changed = replace(command, inputs=replace(command.inputs, bid=command.inputs.bid+.01))
        elif mutation == 'future-low':
            changed = replace(command, inputs=replace(command.inputs, low_boundary_ms=60000))
        elif mutation == 'future-break':
            changed = replace(command, inputs=replace(command.inputs,
                breaks=(replace(command.inputs.breaks[0], completed_boundary_ms=40100),)))
        elif mutation == 'prior':
            changed = replace(command, inputs=replace(command.inputs,
                previous=replace(command.inputs.previous, boundary_ms=40000)))
        elif mutation == 'claim':
            changed = replace(command, transition=replace(command.transition,
                state=replace(command.transition.state, stop=command.transition.state.stop+.01)))
        else:
            changed = replace(command, exit_inputs=replace(command.exit_inputs,
                completed=replace(command.exit_inputs.completed,
                    bid=command.context.source.initial_stop,
                    completed_five_second_close_int=round(command.context.source.initial_stop*10000),
                    macd_line=.01, macd_signal=.02)))
        changed.replay()


def test_protection_transition_and_break_nested_payloads_cannot_mutate(manager):
    command = asyncio.run(protection_command(manager))
    with pytest.raises(TypeError):
        command.transition.stop_amendment['stop_price'] = 0
    with pytest.raises(TypeError):
        command.inputs.breaks[0].level['upper'] = 100
    assert command.replay() == command.transition


def test_session_replay_requires_declared_deadline_and_completed_producer_rows(manager):
    async def run():
        m, runtime, _, _, _, _ = manager
        view = await held(manager)
        await m.on_management(view, {}, 19740000)
        command = runtime.commands[-1]
        assert type(command) is DeclaredSessionCommand
        assert command.replay() == 19740000
        with pytest.raises(ValueError):
            replace(command, context=replace(command.context, boundary_ms=19739900)).replay()
        with pytest.raises(ValueError):
            replace(command, resolutions={100: {'boundary_ms': 19740100}}).replay()
        with pytest.raises(ValueError):
            replace(command, resolutions={True: {}}).replay()
        with pytest.raises(ValueError):
            replace(command, resolutions={0: {}}).replay()
    asyncio.run(run())
