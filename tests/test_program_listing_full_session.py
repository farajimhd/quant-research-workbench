"""Synthetic transport-scale qualification; no real market coverage is asserted."""
import asyncio
from dataclasses import replace

import pytest
from polars.testing import assert_frame_equal

from src.backend.backtest_program_listing_cache import ProgramListingFeatureCache
from src.backend.backtest_program_inputs import prepare_program_block
from src.backend.qmd_columnar_history import CertifiedHistoryPin
from src.backend.qmd_columnar_history_transport import CONTRACT
from src.market_engine import columnar_history_product as c
from src.trading_runtime.columnar_rule_program import evaluate_rules, RuleProgram, RuleNode as N, Operation as O
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from pipelines.market_sip.events.columnar_history_producer import wire_records
from tests.test_backtest_program_inputs import release_for
from tests.test_columnar_strategy_release import bundle
from tests.test_columnar_history_publication import prepared_source, Store, publish, consume


def test_ten_complete_listing_origins_exceed_aggregate_envelope_without_reset(prepared_source):
    store = Store()
    publish(store, prepared_source)
    base = consume(store, prepared_source).pin()
    pins = {}
    for index in range(10):
        scope = replace(base.scope, listing_id=f"L{index:02}", ticker=f"T{index:02}", first_bucket=14400, stop_bucket=72000)
        header = {**base.header, **scope.prefix(), "elapsed_clock_count": scope.clock_count,
            "clock_row_count": scope.clock_count, "value_row_count": len(base.fields)}
        header["field_content_hash"] = c.records_hash(c.FIELD, ({**scope.prefix(), "field_id": field.field_id,
            "field_name": field.name, "unit": field.unit} for field in base.fields))
        header["content_hash"] = c.content_hash({key: value for key, value in header.items()
            if key not in {"content_hash", "certified_at"}})
        pins[scope.listing_id] = CertifiedHistoryPin(scope, base.fields, base.parents, header)
    release = release_for(next(iter(pins.values())))
    spec, _, identity = bundle()
    field, = release.fields.values()
    program = RuleProgram((N(O.FEATURE, field=field.name), N(O.MEAN, left=0, window=30),
        N(O.LAG, left=1, window=60), N(O.CONSTANT, literal=0., unit=field.unit),
        N(O.GREATER, left=2, right=3)), 4)
    release = restore_release(project_release(replace(spec, programs={stage: program for stage in spec.programs}),
        release.fields, **identity))
    calls = []
    def post(path, request, timeout):
        pin = pins[request["scope"]["listing_id"]]
        first, stop = request["first_ordinal"], request["stop_ordinal"]
        calls.append((pin.scope.listing_id, first, stop))
        count = stop - first
        return dict(contract=CONTRACT, coverage=wire_records(c.COVERAGE, [dict(pin.header)])[0],
            first_ordinal=first, stop_ordinal=stop, observed_ordinal=list(range(first, stop)),
            bucket_index=list(range(pin.scope.first_bucket + first, pin.scope.first_bucket + stop)),
            source_known=[True] * count, observed=[True] * count, extremes_valid=[True] * count,
            reference_close=[10.] * count, share_volume=[1.] * count,
            execution_notional=[10.] * count, rolling_volume=[30.] * count,
            fields=[dict(field_id=field.field_id, field_name=field.name, unit=field.unit,
                first_ordinal=[0], stop_ordinal=[pin.scope.clock_count], value_bits=[0x3f800000], valid=[True])
                for field in pin.fields if field.field_id in request["field_ids"]])
    pin = next(iter(pins.values()))
    kwargs = dict(first_ordinal=0, stop_ordinal=pin.scope.clock_count, evaluation_block="full-origin",
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=2, history_post=post)
    async def run():
        with pytest.raises(ValueError, match="preparation bounds"):
            await prepare_program_block(release, pins, **kwargs)
        assert not calls
        # Explicit synthetic test memory budget; never a production default.
        cache = ProgramListingFeatureCache(maximum_retained_bytes=128 * 1024 * 1024)
        blocks = await cache.load(release, pins, **kwargs)
        assert sum(block.market.height for block in blocks.values()) == 576000
        assert 0 < cache.retained_bytes <= cache.maximum_retained_bytes
        for block in blocks.values():
            assert block.first_ordinal == 0 and block.stop_ordinal == 57600
            assert_frame_equal(block.masks, evaluate_rules(block.market, release.programs, release.fields), check_exact=True)
            # Constant valid input: 30-row mean plus 60-row lag has exactly
            # 89 invalid leading rows, with no reset at any transport packet.
            assert block.masks["entry__valid"].head(89).sum() == 0
            assert block.masks["entry"].slice(89).sum() == 57600 - 89
        before = len(calls)
        assert await cache.load(release, pins, **kwargs) is blocks
        assert len(calls) == before
    asyncio.run(run())
