"""Generic V19 candidate translation without publication or market I/O."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest
import torch

from research.vectorized_backtest.v19.features import CATALOG as SOURCE_CATALOG
from research.vectorized_backtest.v19.genome import Individual, Policy, effective_rules
from research.vectorized_backtest.v19.program import Node, Op, Program, TorchPrograms
from src.market_engine.qmd_feature_catalog import CATALOG
from src.trading_runtime.columnar_rule_program import InputField, evaluate_rules
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.trading_runtime.optimized_candidate import (
    compile_candidate, content_hash, required_candidate_fields, restore_candidate,
)
from src.trading_runtime.optimized_program_specification import STAGES, WINNER_FIELDS


FEATURE = next(i for i, f in enumerate(CATALOG) if f.name == 'indicator.ema.3.relative')


def source_member():
    entry = Program((Node(0, feature=FEATURE), Node(1, value=.1), Node(6, a=0, b=1),
        Node(21, a=2), Node(23, a=3, window=3), Node(1, value=.2), Node(6, a=4, b=5)), 6)
    # A valid but inactive dimensional literal exercises full-catalog validation.
    entry = Program(entry.nodes + (Node(1, unit='log_price'),), entry.output)
    never = Program((Node(1, unit='bool'),), 0)
    management = Program((Node(0, feature=FEATURE), Node(1), Node(8, a=0, b=1)), 2)
    return Individual({s: entry if s == 'entry' else never for s in STAGES},
        Policy(swing_left=3, swing_right=2),
        {s: management if s != 'entry' else never for s in STAGES},
        {'entry': 99, 'add': 7, 'exit': 3}).validate()


def envelope():
    program = source_member().payload()
    return dict(version='v19', generation=11, serial=12, candidate=13, program=program,
        execution=dict(entry_dollars=1000., add_dollars=200., cost_bps=0.,
            fee_per_share=.005, minimum_order_fee=1., risk_protection=True,
            maximum_stop_distance=.1, maximum_position_risk=100.),
        universe=dict(version='v6-volume-market-blocks-v1', window=30, top_n=10,
            minimum_close=.8, maximum_close=50.), program_hash=content_hash(program),
        evidence_hash='b' * 64)


def fields_for(candidate):
    by_name = {f.name: f for f in CATALOG}
    return {name: InputField(name, by_name[name].unit,
        'q_live.qmd_causal_history_value_v1', 'a' * 64, 1000)
        for name in required_candidate_fields(candidate)}


def rehash(value):
    value['program_hash'] = content_hash(value['program'])
    return value


def test_actual_genome_compiles_flat_entry_and_open_management_with_projected_fields():
    assert [(f.name, f.unit) for f in CATALOG] == [(f.name, f.unit) for f in SOURCE_CATALOG]
    assert FEATURE not in WINNER_FIELDS
    candidate = restore_candidate(envelope())
    fields = fields_for(candidate)
    assert set(fields) == {CATALOG[FEATURE].name, 'history.swing_low.3.2'}
    specification = compile_candidate(candidate, fields)
    assert specification.programs['entry'].nodes[3].operation == Op.BOOL_RATIO
    assert specification.programs['entry'].nodes[4].operation == Op.STD
    assert all(specification.programs[s].nodes[2].operation == Op.LESS
               for s in STAGES if s != 'entry')
    assert specification.minimum_age_seconds == {'entry': 0, 'add': 7, 'exit': 3}
    assert specification.policy['swing_left'] == 3
    assert specification.policy['cooldown_seconds'] == 30
    assert specification.winner_hash == candidate.program_hash


def test_translated_lifecycle_outputs_and_masks_match_v19_torch():
    candidate = restore_candidate(envelope())
    fields = fields_for(candidate)
    specification = compile_candidate(candidate, fields)
    member = source_member()
    programs = effective_rules(member)
    values = np.random.default_rng(190).normal(size=(140, len(CATALOG))).astype(np.float32)
    known = np.ones_like(values, dtype=bool)
    known[6:9, FEATURE] = False
    values[25, FEATURE] = np.nan
    values[50, FEATURE] = np.inf
    expected, masks = TorchPrograms([programs[s] for s in STAGES], SOURCE_CATALOG)(
        torch.from_numpy(values), torch.from_numpy(known))
    times = [datetime(2026, 8, 3, tzinfo=timezone.utc) + timedelta(seconds=i)
             for i in range(len(values))]
    columns = dict(listing_id=['a'] * len(values), evaluation_block=[0] * len(values),
                   observed_ordinal=range(len(values)), available_at=times, boundary_at=times)
    for i, field in enumerate(CATALOG):
        if field.name in fields:
            columns[field.name] = values[:, i].tolist()
            columns[field.name + '__valid'] = known[:, i].tolist()
    actual = evaluate_rules(pl.DataFrame(columns), specification.programs, fields)
    for i, stage in enumerate(STAGES):
        assert actual[stage].to_list() == ((expected[i] != 0) & masks[i]).tolist()
        assert actual[stage + '__valid'].to_list() == masks[i].tolist()


def test_no_open_rules_falls_back_to_flat_management():
    raw = envelope()
    raw['program']['open_rules'] = None
    candidate = restore_candidate(rehash(raw))
    specification = compile_candidate(candidate, fields_for(candidate))
    assert all(specification.programs[s].nodes[0].literal == 0
               for s in STAGES if s != 'entry')


def test_normalized_release_roundtrip_preserves_translated_rules_and_ages():
    candidate = restore_candidate(envelope())
    fields = fields_for(candidate)
    specification = compile_candidate(candidate, fields)
    rows = project_release(specification, fields, strategy_number=63,
        strategy_id='candidate-v19', release_id='00000000-0000-0000-0000-000000000063',
        code_hash='c' * 64, universe_source_hash='d' * 64,
        published_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
    restored = restore_release(rows)
    assert restored.programs == specification.programs
    assert restored.fields == fields
    assert restored.minimum_age_seconds == {
        stage: specification.minimum_age_seconds.get(stage, 0) for stage in STAGES}
    assert float(restored.header['entry_cash']) == 1000.


def test_hash_tampering_is_rejected_on_restore_and_compile():
    raw = envelope()
    raw['program']['minimum_age']['add'] = 8
    with pytest.raises(ValueError, match='hash differs'):
        restore_candidate(raw)
    candidate = restore_candidate(envelope())
    fields = fields_for(candidate)
    candidate.program['minimum_age']['add'] = 8
    with pytest.raises(ValueError, match='hash differs'):
        compile_candidate(candidate, fields)


@pytest.mark.parametrize('feature', [-1, len(CATALOG), True, 1.5])
def test_malformed_feature_ids_fail_closed(feature):
    raw = envelope()
    raw['program']['rules']['entry']['nodes'][0]['feature'] = feature
    candidate = restore_candidate(rehash(raw))
    with pytest.raises(ValueError, match='feature ID'):
        required_candidate_fields(candidate)


def test_unknown_literal_unit_and_missing_or_mismatched_fields_fail_closed():
    raw = envelope()
    raw['program']['rules']['entry']['nodes'][-1]['unit'] = 'invented_unit'
    candidate = restore_candidate(rehash(raw))
    with pytest.raises(ValueError, match='literal unit'):
        compile_candidate(candidate, fields_for(candidate))
    candidate = restore_candidate(envelope())
    fields = fields_for(candidate)
    with pytest.raises(ValueError, match='certified QMD'):
        compile_candidate(candidate, {})
    name = CATALOG[FEATURE].name
    fields[name] = replace(fields[name], product='arte.history_feature_v1', unit='price')
    with pytest.raises(ValueError, match='shared feature catalog'):
        compile_candidate(candidate, fields)
