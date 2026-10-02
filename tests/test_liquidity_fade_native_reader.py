"""Native row codec/cold-read checks; no connected writes or launch admission."""
from dataclasses import replace
from datetime import date
import json

import pytest

from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import typed_row, _canonical_typed_content
from src.trading_runtime.arte_liquidity_fade_failure_v4 import (
    LIQUIDITY_FADE_FAILURE, project_liquidity_fade_failure, CHECKPOINT_REFERENCE_FIELDS,
)
from src.trading_runtime.arte_liquidity_fade_reader_v4 import load_liquidity_fade_failure
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeCandle
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY


class Client:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def execute(self, query):
        self.queries.append(query)
        return '\n'.join(json.dumps(row) for row in self.rows)


def native_case():
    witness, _, _, row = prepared_case()
    sealed = typed_row(LIQUIDITY_FADE_FAILURE.name, row)
    stored = {**_canonical_typed_content(LIQUIDITY_FADE_FAILURE.name, row),
              'content_hash': sealed['content_hash']}
    # ClickHouse JSONEachRow commonly quotes UInt64, not UInt32.
    for name, kind in LIQUIDITY_FADE_FAILURE.columns:
        if kind == 'UInt64':
            stored[name] = str(stored[name])
    prefix = V4CommittedPrefix(row['run_id'], 65, IDENTITY, 'cursor', 'running', (IDENTITY,))
    return witness, row, stored, prefix


def test_native_row_roundtrip_and_single_bounded_read():
    witness, row, stored, prefix = native_case()
    client = Client([stored])
    raw, recovered = load_liquidity_fade_failure(client, prefix, row['parent_record_id'])
    assert recovered == witness and raw == stored
    assert len(client.queries) == 1
    assert 'LIMIT 2 FORMAT JSONEachRow' in client.queries[0]
    assert 'FROM arte.trading_liquidity_fade_failure_v4 ' in client.queries[0]


def test_native_counts_above_float_precision_are_retained_exactly():
    witness, financial, _, row = prepared_case()
    counts = (2**64-1, 2**64-1, 2**53+1, 2**53+3)
    witness = replace(witness, candles=tuple(LiquidityFadeCandle(c.boundary_ms, n)
                      for c, n in zip(witness.candles, counts, strict=True)))
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id=IDENTITY)
    intent = liquidity_fade_exit_intent(witness, financial, **args)
    projected = project_liquidity_fade_failure(witness, intent, financial, **args,
        **{key: row[key] for key in ('run_id', 'batch_id', 'parent_record_id',
           'source_build_id', 'source_market_plan_token', 'source_bars_attempt_id',
           'source_indicators_attempt_id', 'source_liquidity_attempt_id', *CHECKPOINT_REFERENCE_FIELDS)})
    sealed = typed_row(LIQUIDITY_FADE_FAILURE.name, projected)
    for i in range(4):
        sealed[f'trade_count_{i}'] = str(sealed[f'trade_count_{i}'])
    prefix = native_case()[-1]
    _, recovered = load_liquidity_fade_failure(Client([sealed]), prefix, row['parent_record_id'])
    assert tuple(c.trade_count for c in recovered.candles) == counts


@pytest.mark.parametrize('field,value', [('trade_count_0', '58'), ('bid', '2.30'),
    ('source_build_id', 'c'*64), ('content_hash', '0'*64)])
def test_stored_content_tampering_rejects_before_predicate_replay(field, value):
    _, row, stored, prefix = native_case()
    stored[field] = value
    with pytest.raises(RuntimeError, match='hash'):
        load_liquidity_fade_failure(Client([stored]), prefix, row['parent_record_id'])


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'foreign_batch', 'foreign_run', 'foreign_parent'])
def test_missing_ambiguous_or_uncommitted_row_rejects(mode):
    _, row, stored, prefix = native_case()
    rows = [stored]
    if mode == 'missing': rows = []
    elif mode == 'duplicate': rows *= 2
    else:
        field = dict(foreign_batch='batch_id', foreign_run='run_id', foreign_parent='parent_record_id')[mode]
        stored[field] = 'foreign'
    with pytest.raises(RuntimeError, match='unique committed'):
        load_liquidity_fade_failure(Client(rows), prefix, row['parent_record_id'])


def test_rehashed_wrong_child_identity_cannot_be_adopted():
    _, row, _, prefix = native_case()
    row['record_id'] = '00000000-0000-0000-0000-000000000001'
    stored = typed_row(LIQUIDITY_FADE_FAILURE.name, row)
    with pytest.raises(RuntimeError, match='deterministic'):
        load_liquidity_fade_failure(Client([stored]), prefix, row['parent_record_id'])


@pytest.mark.parametrize('change', [dict(batch_ids=()), dict(last_batch_id='other'),
    dict(last_sequence=0), dict(last_sequence=True), dict(status='unknown')])
def test_invalid_prefix_rejects_before_query(change):
    _, row, stored, prefix = native_case()
    client = Client([stored])
    with pytest.raises(ValueError):
        load_liquidity_fade_failure(client, replace(prefix, **change), row['parent_record_id'])
    assert not client.queries


def test_installed_codec_accepts_exact_compound_envelope_but_rejects_subclasses():
    from src.trading_runtime.arte_journal_compound_v4 import _unit_children, _publication_kwargs
    from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
    from test_liquidity_fade_prepared_transport import transport
    row, base = transport()
    unit = V4LiquidityFadeFailureBatch(base, row)
    assert _unit_children(unit) == (("liquidity_fade_failures", row),)
    assert _publication_kwargs(unit) == {"liquidity_fade_rows": (row,)}
    class ForeignEnvelope(V4LiquidityFadeFailureBatch):
        pass
    foreign = ForeignEnvelope(base, row)
    for check in (_unit_children, _publication_kwargs):
        with pytest.raises(TypeError):
            check(foreign)


def test_new_scalar_contract_retains_explicit_ssd_policy():
    ddl = LIQUIDITY_FADE_FAILURE.ddl()
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert 'ENGINE = MergeTree' in ddl
    assert 'trading_liquidity_fade_failure_v4' in ddl
