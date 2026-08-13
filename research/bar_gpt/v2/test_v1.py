from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import torch
import polars as pl
from rich.console import Console

from research.bar_gpt.v2.build_1s import (
    BuildReporter,
    PREVIOUS_COHORT_TABLE,
    _query_tsv,
    _show_create_raw,
    create_target_table_sql,
    drop_previous_cohort_tables_first_run,
    insert_one_second_sql,
    ticker_fingerprint,
)
from research.bar_gpt.v2.trade_correction_overlay import (
    CORRECTION_OVERLAY_VERSION,
    DEFAULT_CORRECTION_OVERLAY_TABLE,
    SourceFileIdentity,
    overlay_insert_sql,
    overlaid_event_source_sql,
    staged_records_insert_sql,
)
from research.bar_gpt.v2.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_COHORT_2TB_SHA256,
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_COHORT_5TB_300,
    BAR_GPT_COHORT_5TB_300_SHA256,
    BAR_GPT_IDENTITY_HOLDOUT_TICKERS,
    BAR_GPT_IDENTITY_QUARANTINE,
    BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
    BAR_GPT_SOURCE_ALIAS_TICKERS,
    BAR_GPT_TRAINING_TICKERS,
    BAR_GPT_VALIDATION_SLICES_2026,
)
from research.bar_gpt.v2.config import BarGPTConfig, DataConfig
from research.bar_gpt.v2.data import BarView, causal_asof_indices, densify_one_second_view, horizon_target_indices, rollup_intraday_view
from research.bar_gpt.v2.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v2.direct_event_shards import direct_trade_bar_query
from research.bar_gpt.v2.model import (
    BarGPTV2,
    CausalSelfAttention,
    RMSNorm,
    _fused_linear_group,
    _rotate_half,
)
from research.bar_gpt.v2.loader import (
    ClickHouseBarStreamConfig,
    TickerInterval,
    daily_range_query,
    daily_session_frame_to_view,
    daily_tickers_range_query,
    ticker_range_query,
)
from research.bar_gpt.v2.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v2.targets import (
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    TARGET_NAMES,
    build_next_bar_targets,
    build_physical_horizon_targets,
)
from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import insert_intraday_condition_bars_sql, parse_args as parse_intraday_args
from pipelines.market_sip.events.clickhouse_build_daily_session_bars import (
    insert_session_bars_sql,
    parse_args as parse_daily_args,
)
from pipelines.market_sip.events.clickhouse_build_unified_events import (
    DEFAULT_DROP_TRADE_CORRECTION_CODES,
    parse_trade_correction_codes,
)
from research.bar_gpt.v2.run_build_1s import main as launcher_main
from research.bar_gpt.v2.run_build_1s import parse_args as parse_launcher_args
from research.bar_gpt.v2.run_build_1s_aliases import parse_args as parse_alias_launcher_args


def builder_args() -> argparse.Namespace:
    return argparse.Namespace(
        database="market_sip_compact",
        target_table="bar_gpt_1s_bars_v1",
        events_table_base="events",
        condition_reference_table="event_condition_token_reference",
        correction_overlay_table=DEFAULT_CORRECTION_OVERLAY_TABLE,
        max_quote_spread_bps=1000.0,
        storage_policy="ssd_policy",
        max_threads=2,
        max_memory_usage="4G",
        max_bytes_before_external_group_by="1G",
        correction_record_table="bar_gpt_trade_correction_records_v1",
        correction_record_manifest_table="bar_gpt_trade_correction_record_manifest_v1",
        max_memory_usage_bytes=4 * 1024**3,
    )


class BuilderSqlTest(unittest.TestCase):
    def test_direct_event_query_requires_trade_for_every_sparse_token(self) -> None:
        config = DataConfig(
            tickers=("AAPL",), start_date="2026-01-01", end_date="2026-02-01",
            validation_start_date="2026-01-01",
            validation_slices=(("AAPL", "2026-01-01", "2026-02-01"),),
        )
        sql = direct_trade_bar_query(
            config,
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="AAPL",
            start_date="2026-01-02",
            end_date="2026-01-03",
            source_intervals=(TickerInterval("AAPL", "AAPL", "2019-01-01", "9999-12-31"),),
        )
        self.assertIn("countIf(trade_origin_eligible) > 0) AS context_eligible", sql)
        self.assertIn("countIf(trade_origin_eligible) > 0) AS origin_eligible", sql)
        self.assertIn("HAVING eligible_trade_event_count>0", sql)
        self.assertNotIn("bar_gpt_trade_correction_overlay", sql)
        self.assertIn("FORMAT ArrowStream", sql)

    def test_correction_overlay_preserves_both_tape_times_and_swaps_payloads(self) -> None:
        args = builder_args()
        sql = overlay_insert_sql(args, dt.date(2026, 8, 7))
        self.assertIn("countIf(correction=1) AS count_01", sql)
        self.assertIn("countIf(correction=12) AS count_12", sql)
        self.assertIn("sip_timestamp_us_01 AS target_sip_timestamp_us", sql)
        self.assertIn("event_meta_12 AS replacement_event_meta", sql)
        self.assertIn("sip_timestamp_us_12,\n        event_meta_12", sql)
        self.assertIn("event_meta_01", sql)
        self.assertIn("FROM `market_sip_compact`.`events_2026` AS e", sql)
        self.assertIn("INNER JOIN expected AS x", sql)
        self.assertIn(CORRECTION_OVERLAY_VERSION, sql)

    def test_correction_record_scan_is_bounded_to_pair_codes(self) -> None:
        args = builder_args()
        source = SourceFileIdentity(
            source_date=dt.date(2026, 8, 7),
            path_win=r"D:\market-data\flatfiles\us_stocks_sip\trades_v1\2026\08\2026-08-07.csv.gz",
            path_ch="/mnt/d/market-data/flatfiles/us_stocks_sip/trades_v1/2026/08/2026-08-07.csv.gz",
            size=123,
            mtime_ns=456,
        )
        sql = staged_records_insert_sql(args, source)
        self.assertIn("toInt16OrZero(correction) IN (1, 12)", sql)
        self.assertNotIn("NOT IN (7, 8, 10, 11, 12)", sql)
        self.assertIn("CSVWithNames", sql)

    def test_overlaid_source_covers_both_utc_dates_for_new_york_day(self) -> None:
        args = builder_args()
        sql = overlaid_event_source_sql(args, dt.date(2026, 7, 24), ("AAPL",))
        self.assertIn("event_date>=toDate('2026-07-24')", sql.replace(" ", ""))
        self.assertIn("event_date<toDate('2026-07-26')", sql.replace(" ", ""))
        self.assertIn("o.source_date=e.event_date", sql)
        self.assertIn("e.ticker IN ('AAPL')", sql)

    def test_rms_norm_preserves_fp32_reference_numerics(self) -> None:
        value = torch.randn(2, 5, 16, dtype=torch.bfloat16)
        norm = RMSNorm(16)
        actual = norm(value)
        value_float = value.float()
        expected = (
            value_float
            * torch.rsqrt(value_float.pow(2).mean(dim=-1, keepdim=True) + norm.eps)
        ).to(dtype=value.dtype) * norm.weight
        torch.testing.assert_close(actual, expected)

    def test_cpu_causal_attention_uses_compatible_explicit_gqa(self) -> None:
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.0)
        attention = CausalSelfAttention(config).eval()
        value = torch.randn(2, 7, 32)
        with patch(
            "research.bar_gpt.v2.model.F.scaled_dot_product_attention",
            wraps=torch.nn.functional.scaled_dot_product_attention,
        ) as sdpa:
            output = attention(value)
        self.assertEqual(output.shape, value.shape)
        query, key, val = sdpa.call_args.args[:3]
        self.assertEqual(query.shape[1], 4)
        self.assertEqual(key.shape[1], 4)
        self.assertEqual(val.shape[1], 4)
        self.assertFalse(sdpa.call_args.kwargs["enable_gqa"])

    def test_local_attention_expands_kv_for_fused_sdpa_dispatch(self) -> None:
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.0)
        attention = CausalSelfAttention(config).eval()
        value = torch.randn(2, 7, 32)
        with patch(
            "research.bar_gpt.v2.model.F.scaled_dot_product_attention",
            wraps=torch.nn.functional.scaled_dot_product_attention,
        ) as sdpa:
            output = attention(value, attention_window=3)
        self.assertEqual(output.shape, value.shape)
        query, key, val = sdpa.call_args.args[:3]
        self.assertEqual(query.shape[1], 4)
        self.assertEqual(key.shape[1], 4)
        self.assertEqual(val.shape[1], 4)
        self.assertFalse(sdpa.call_args.kwargs["is_causal"])
        allowed = sdpa.call_args.kwargs["attn_mask"]
        self.assertFalse(bool(torch.triu(allowed, diagonal=1).any()))

    def test_fused_local_attention_matches_dense_reference_outputs_and_gradients(self) -> None:
        torch.manual_seed(19)
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.0)
        attention = CausalSelfAttention(config).eval()
        actual_input = torch.randn(2, 11, 32, requires_grad=True)
        reference_input = actual_input.detach().clone().requires_grad_(True)
        token_mask = torch.tensor([
            [False, False, True, True, True, True, True, True, True, True, True],
            [True, True, True, True, True, True, True, True, False, False, False],
        ])
        actual = attention(actual_input, attention_window=4, token_mask=token_mask)

        batch, length, _ = reference_input.shape
        query = attention.q_proj(reference_input).view(batch, length, 4, 8).transpose(1, 2)
        key = attention.k_proj(reference_input).view(batch, length, 2, 8).transpose(1, 2)
        value = attention.v_proj(reference_input).view(batch, length, 2, 8).transpose(1, 2)
        query = attention.q_norm(query)
        key = attention.k_norm(key)
        cosine, sine = attention.rope(length, reference_input.device, reference_input.dtype)
        query = query * cosine + _rotate_half(query) * sine
        key = key * cosine + _rotate_half(key) * sine
        positions = torch.arange(length)
        allowed = (
            (positions[None, :] <= positions[:, None])
            & (positions[None, :] > positions[:, None] - 4)
        ).view(1, 1, length, length) & token_mask[:, None, None, :]
        invalid_query = ~token_mask
        allowed |= invalid_query[:, None, :, None] & torch.eye(
            length, dtype=torch.bool
        ).view(1, 1, length, length)
        reference = torch.nn.functional.scaled_dot_product_attention(
            query,
            key.repeat_interleave(2, dim=1),
            value.repeat_interleave(2, dim=1),
            attn_mask=allowed,
        )
        reference = attention.out_proj(reference.transpose(1, 2).contiguous().view(batch, length, -1))
        reference = reference * token_mask.unsqueeze(-1)
        torch.testing.assert_close(actual, reference, atol=2e-5, rtol=2e-5)

        actual.square().sum().backward()
        reference.square().sum().backward()
        torch.testing.assert_close(
            actual_input.grad,
            reference_input.grad,
            atol=3e-5,
            rtol=3e-5,
        )

    def test_future_tokens_cannot_change_past_states_in_either_attention_path(self) -> None:
        torch.manual_seed(17)
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.0)
        attention = CausalSelfAttention(config).eval()
        value = torch.randn(2, 9, 32)
        changed = value.clone()
        changed[:, 5:] = torch.randn_like(changed[:, 5:]) * 100.0

        for label, kwargs in (
            ("native causal", {}),
            ("explicit causal window", {"attention_window": 4}),
            ("explicit causal padding", {"token_mask": torch.ones(2, 9, dtype=torch.bool)}),
        ):
            with self.subTest(path=label), torch.no_grad():
                expected = attention(value, **kwargs)
                actual = attention(changed, **kwargs)
            torch.testing.assert_close(actual[:, :5], expected[:, :5], atol=1e-6, rtol=1e-6)

    def test_long_local_attention_remains_causal(self) -> None:
        torch.manual_seed(23)
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.0)
        attention = CausalSelfAttention(config).eval()
        value = torch.randn(1, 270, 32)
        changed = value.clone()
        changed[:, 257:] += torch.randn_like(changed[:, 257:]) * 100.0
        with torch.no_grad():
            expected = attention(value, attention_window=17)
            actual = attention(changed, attention_window=17)
        torch.testing.assert_close(actual[:, :257], expected[:, :257], atol=2e-5, rtol=2e-5)

    def test_window_attention_dropout_remains_active_and_reproducible(self) -> None:
        config = BarGPTConfig(d_model=32, n_heads=4, n_kv_heads=2, dropout=0.25)
        attention = CausalSelfAttention(config).train()
        value = torch.randn(2, 19, 32)
        torch.manual_seed(31)
        first = attention(value, attention_window=5)
        torch.manual_seed(31)
        second = attention(value, attention_window=5)
        torch.testing.assert_close(first, second)
        attention.eval()
        deterministic = attention(value, attention_window=5)
        self.assertFalse(torch.allclose(first, deterministic))

    def test_native_gqa_matches_explicit_kv_repetition(self) -> None:
        query = torch.randn(2, 4, 7, 8, requires_grad=True)
        key = torch.randn(2, 2, 7, 8, requires_grad=True)
        value = torch.randn(2, 2, 7, 8, requires_grad=True)
        local_mask = torch.ones(7, 7, dtype=torch.bool).tril()
        native = torch.nn.functional.scaled_dot_product_attention(
            query,
            key,
            value,
            attn_mask=local_mask,
            enable_gqa=True,
        )
        explicit = torch.nn.functional.scaled_dot_product_attention(
            query,
            key.repeat_interleave(2, dim=1),
            value.repeat_interleave(2, dim=1),
            attn_mask=local_mask,
        )
        torch.testing.assert_close(native, explicit)
        native.sum().backward(retain_graph=True)
        native_gradients = (query.grad.clone(), key.grad.clone(), value.grad.clone())
        query.grad = key.grad = value.grad = None
        explicit.sum().backward()
        for actual, expected in zip(
            (query.grad, key.grad, value.grad),
            native_gradients,
            strict=True,
        ):
            torch.testing.assert_close(actual, expected)

    def test_condition_sidecar_materializes_only_flagged_event_seconds(self) -> None:
        args = parse_intraday_args([
            "--date", "2020-01-02", "--artifact-mode", "conditions-only",
            "--resolutions", "1s", "--tickers", "AAPL",
        ])
        sql = insert_intraday_condition_bars_sql(
            args=args, dates=[dt.date(2020, 1, 2)], resolutions_us=(1_000_000,)
        )
        self.assertIn("WHERE `condition_halt_pause_flag_event` > 0 OR", sql)
        self.assertNotIn("WITH FILL", sql.upper())
        self.assertNotIn("numbers(", sql)

    def test_canonical_two_tb_cohort_is_unique_and_fingerprinted(self) -> None:
        self.assertEqual(len(BAR_GPT_COHORT_2TB), 100)
        self.assertEqual(len(set(BAR_GPT_COHORT_2TB)), 100)
        self.assertEqual(BAR_GPT_COHORT_2TB_SHA256, "bb04a7c59d341d62d2fbf7758efa8ac175ae5ff4ba8400972f2517cd3896432c")
        self.assertEqual(ticker_fingerprint(tuple(sorted(BAR_GPT_COHORT_2TB))), BAR_GPT_COHORT_2TB_SHA256)
        for representative in ("SPY", "AAPL", "UVXY", "COIN", "XBIO", "ATOS"):
            self.assertIn(representative, BAR_GPT_COHORT_2TB)

    def test_launcher_and_training_data_default_to_canonical_cohort(self) -> None:
        args, extra = parse_launcher_args([])
        self.assertFalse(extra)
        self.assertEqual(tuple(args.tickers.split(",")), BAR_GPT_COHORT_2TB)
        self.assertEqual(args.target_table, BAR_GPT_COHORT_2TB_TABLE)
        self.assertEqual(args.manifest_table, BAR_GPT_COHORT_2TB_MANIFEST_TABLE)
        self.assertEqual(args.start_date, "2019-01-01")
        self.assertEqual(DataConfig().one_second_table, BAR_GPT_COHORT_2TB_TABLE)
        self.assertEqual(DataConfig().tickers, BAR_GPT_TRAINING_TICKERS)
        self.assertEqual(BAR_GPT_IDENTITY_QUARANTINE, ("MOGO",))
        self.assertEqual(len(BAR_GPT_COHORT_5TB_300), 300)
        self.assertEqual(len(set(BAR_GPT_COHORT_5TB_300)), 300)
        self.assertEqual(
            BAR_GPT_COHORT_5TB_300_SHA256,
            "069d7b781ffe6d7dfa4d4168f7fde7791cf79d9a115418cb77820e2eae07651d",
        )
        self.assertEqual(len(BAR_GPT_VALIDATION_SLICES_2026), 300)
        self.assertEqual(
            tuple(ticker for ticker, _start, _end in BAR_GPT_VALIDATION_SLICES_2026),
            BAR_GPT_TRAINING_TICKERS,
        )
        self.assertTrue(all(
            (start, end) == ("2026-01-01", "2026-08-01")
            for _ticker, start, end in BAR_GPT_VALIDATION_SLICES_2026
        ))
        self.assertEqual(DataConfig().validation_blocks_per_slice, 2)
        self.assertEqual(
            set(BAR_GPT_TRAINING_TICKERS) - set(DataConfig().training_tickers),
            set(BAR_GPT_IDENTITY_HOLDOUT_TICKERS),
        )
        self.assertEqual(len(DataConfig().training_tickers), 292)

    def test_data_config_accepts_single_ticker_for_bounded_shard_builds(self) -> None:
        config = DataConfig(
            tickers=("GOOGL",),
            validation_slices=(("GOOGL", "2026-01-01", "2026-08-01"),),
        )

        config.validate()

    def test_custom_tickers_cannot_contaminate_canonical_tables(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Custom --tickers require custom"):
            launcher_main(["--tickers", "AAPL"])

    def test_first_run_legacy_drop_is_exactly_guarded(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.drops: list[str] = []

            def execute(self, query: str, **_kwargs) -> str:
                if query.startswith("DROP TABLE"):
                    self.drops.append(query)
                    return ""
                if "FROM system.tables" in query and "count()" in query:
                    if f"name='{PREVIOUS_COHORT_TABLE}'" in query:
                        return "1\n"
                    return "0\n"
                if "SELECT storage_policy" in query:
                    return "live_market_ssd\n"
                return "0\n"

        args = argparse.Namespace(
            drop_previous_cohort_first_run=True,
            confirm_drop_previous_table=PREVIOUS_COHORT_TABLE,
            target_table=BAR_GPT_COHORT_2TB_TABLE,
            manifest_table=BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
            database="market_sip_compact",
            storage_policy="live_market_ssd",
        )
        client = FakeClient()
        self.assertEqual(
            drop_previous_cohort_tables_first_run(client, args),  # type: ignore[arg-type]
            (PREVIOUS_COHORT_TABLE,),
        )
        self.assertEqual(len(client.drops), 1)
        self.assertIn(f"`{PREVIOUS_COHORT_TABLE}` SYNC", client.drops[0])
        args.confirm_drop_previous_table = "wrong"
        with self.assertRaisesRegex(RuntimeError, "exact confirmation"):
            drop_previous_cohort_tables_first_run(client, args)  # type: ignore[arg-type]

    def test_alias_builder_has_separate_manifest_and_raw_source_tickers(self) -> None:
        args, extra = parse_alias_launcher_args([])
        self.assertFalse(extra)
        self.assertEqual(args.start_date, "2019-01-01")
        self.assertEqual(BAR_GPT_SOURCE_ALIAS_TICKERS, ("FB",))
        self.assertEqual(DataConfig().alias_manifest_table, BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE)

    def test_metadata_queries_use_unescaped_tsv(self) -> None:
        class FakeClient:
            query = ""

            def execute(self, query: str) -> str:
                self.query = query
                return "built_at\tDateTime64(3, 'UTC')\n"

        client = FakeClient()
        rows = _query_tsv(client, "SELECT name, type FROM system.columns")  # type: ignore[arg-type]
        self.assertTrue(client.query.endswith("FORMAT TSVRaw"))
        self.assertEqual(rows, [["built_at", "DateTime64(3, 'UTC')"]])

    def test_show_create_uses_unescaped_raw_format(self) -> None:
        class FakeClient:
            query = ""

            def execute(self, query: str, *, query_id: str | None = None) -> str:
                self.query = query
                return "SETTINGS storage_policy = 'live_market_ssd'\n"

        client = FakeClient()
        ddl = _show_create_raw(client, "market_sip_compact", "bar_gpt_1s_bars_v1")  # type: ignore[arg-type]
        self.assertTrue(client.query.endswith("FORMAT TSVRaw"))
        self.assertIn("storage_policy = 'live_market_ssd'", ddl)

    def test_table_contract_uses_requested_policy_and_key(self) -> None:
        sql = create_target_table_sql(builder_args())
        self.assertIn("storage_policy = 'ssd_policy'", sql)
        self.assertIn("PARTITION BY toYYYYMM(local_date)", sql)
        self.assertIn("ORDER BY (ticker, local_date, bucket_index)", sql)
        self.assertIn("ReplacingMergeTree(built_at)", sql)

    def test_insert_is_one_second_only_and_scans_source_once(self) -> None:
        import datetime as dt

        sql = insert_one_second_sql(builder_args(), dt.date(2026, 7, 24), ("AAPL", "MSFT"))
        self.assertNotIn("arrayJoin", sql)
        self.assertEqual(sql.count("FROM `market_sip_compact`.`events_2026`"), 1)
        self.assertIn("bar_gpt_trade_correction_overlay_v1", sql)
        self.assertIn("o.source_date=e.event_date", sql)
        self.assertIn("event_date<toDate('2026-07-26')", sql.replace(" ", ""))
        self.assertNotIn("intraday_condition_bars_by_time_ticker", sql)
        self.assertNotIn("label_resolution_us", sql)
        self.assertIn("microprice", sql)
        self.assertIn("queue_imbalance", sql)
        self.assertIn("ticker IN ('AAPL', 'MSFT')", sql)
        self.assertIn("arrayAll(token -> has(update_last_tokens, token)", sql)
        self.assertIn("countIf(origin_event_eligible)", sql)
        self.assertIn("HAVING origin_event_count > 0", sql)
        self.assertIn("condition_halt_pause_count", sql)
        self.assertIn("unknown_condition_event_count", sql)
        self.assertIn("quote_spread_bps <= 1000", sql)

    def test_unified_event_authority_retains_correction_pair_sides_for_causal_overlay(self) -> None:
        self.assertEqual(parse_trade_correction_codes(DEFAULT_DROP_TRADE_CORRECTION_CODES), [7, 8, 10, 11])

    def test_daily_context_reuses_same_second_level_condition_eligibility(self) -> None:
        args = parse_daily_args([
            "--bar-gpt-condition-eligibility",
            "--tickers", "AAPL",
            "--target-table", "bar_gpt_daily_v2_test",
            "--manifest-table", "bar_gpt_daily_manifest_v2_test",
        ])
        self.assertEqual(args.schema_version, 5)
        self.assertEqual(args.feature_version, "bar_gpt_direct_events_trade_sparse_v5")
        sql = insert_session_bars_sql(args, dt.date(2019, 1, 3), dt.date(2019, 1, 4))
        self.assertIn("second_has_origin", sql)
        self.assertIn("FROM eligible_events", sql)
        self.assertIn("trade_price_eligible_size_sum", sql)
        self.assertIn("arrayAll(token -> has(update_high_low_tokens, token)", sql)
        self.assertIn("bar_gpt_trade_correction_overlay_v1", sql)
        self.assertIn("modifier_int = 12) AS trade_model_ineligible_tokens", sql)
        self.assertIn("modifier_int IN (-1, 12, 15", sql)
        self.assertIn("condition_luld_limit_state_count", sql)
        self.assertIn("countIf(event_retained)) AS source_event_count", sql)

    def test_training_query_is_ordered_incremental_arrow(self) -> None:
        sql = ticker_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="aapl",
            start_date="2026-07-01",
            end_date="2026-08-01",
        )
        self.assertIn("ticker = 'AAPL'", sql)
        self.assertIn("PREWHERE (b.ticker = 'AAPL'", sql)
        self.assertIn("ORDER BY b.ticker, b.local_date, b.bucket_index", sql)
        self.assertIn("max_bytes_before_external_sort = 1073741824", sql)
        self.assertIn("optimize_read_in_order = 1", sql)
        self.assertTrue(sql.strip().endswith("FORMAT ArrowStream"))
        daily_sql = daily_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="aapl",
            start_date="2025-01-01",
            end_date="2026-01-01",
        )
        self.assertIn("source_ticker = 'AAPL'", daily_sql)
        self.assertIn("session_kind", daily_sql)
        self.assertIn("available_at_us", daily_sql)
        batched_daily_sql = daily_tickers_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            tickers=("AAPL", "MSFT"),
            start_date="2025-01-01",
            end_date="2026-01-01",
        )
        self.assertIn("source_ticker = 'AAPL'", batched_daily_sql)
        self.assertIn("source_ticker = 'MSFT'", batched_daily_sql)
        self.assertIn("ORDER BY ticker, local_date, bar_start_us", batched_daily_sql)

    def test_daily_context_uses_the_resolved_point_in_time_source_timeline(self) -> None:
        sql = daily_tickers_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            tickers=("META",),
            start_date="2019-01-01",
            end_date="2024-01-01",
            intervals_by_ticker={
                "META": (
                    TickerInterval("META", "FB", "2012-05-18", "2022-06-09"),
                    TickerInterval("META", "META", "2022-06-09", "9999-12-31"),
                )
            },
        )

        self.assertIn("source_ticker = 'FB'", sql)
        self.assertIn("session_date < toDate('2022-06-09')", sql)
        self.assertIn("source_ticker = 'META'", sql)
        self.assertIn("session_date >= toDate('2022-06-09')", sql)
        self.assertNotIn("canonical_ticker", sql)

    def test_daily_context_fails_closed_on_overlapping_reused_ticker(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "overlaps canonical identities"):
            daily_tickers_range_query(
                ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
                tickers=("CURRENT_A", "CURRENT_B"),
                start_date="2020-01-01",
                end_date="2021-01-01",
                intervals_by_ticker={
                    "CURRENT_A": (TickerInterval("CURRENT_A", "REUSED", "2019-01-01", "2020-07-01"),),
                    "CURRENT_B": (TickerInterval("CURRENT_B", "REUSED", "2020-06-01", "9999-12-31"),),
                },
            )


class BuildReporterTest(unittest.TestCase):
    @staticmethod
    def reporter() -> BuildReporter:
        reporter = BuildReporter(
            report_path=Path(r"D:\TradingML\runtimes\bar_gpt\v2\build_1s\test\build.jsonl"),
            total_days=2_922,
            interactive=True,
        )
        reporter.event = Mock()  # type: ignore[method-assign]
        return reporter

    def test_compact_render_preserves_operational_state_and_throughput(self) -> None:
        reporter = self.reporter()
        reporter.update(stage="building", day="2024-08-01", unit="batch_00127_abcdef012345")
        reporter.completed_days = 1_500
        reporter.skipped_units = 314
        reporter.record_unit_complete(output_rows=1_250_000, source_events=39_875_221, seconds=8.0)
        output = io.StringIO()
        console = Console(file=output, width=60, force_terminal=False, color_system=None)
        reporter._console = console
        console.print(reporter._render())
        rendered = output.getvalue()
        compact_text = " ".join(rendered.split())
        self.assertIn("building", compact_text)
        self.assertIn("batch_00127_abcdef012345", compact_text)
        self.assertIn("1,250,000 rows in 8.0s", compact_text)
        self.assertIn("source events 39,875,221", compact_text)
        self.assertIn("51%", compact_text)

    def test_interruption_and_failure_remain_visible(self) -> None:
        reporter = self.reporter()
        reporter.mark_interrupted("Cancellation requested")
        self.assertTrue(reporter.was_interrupted)
        self.assertEqual(reporter.stage, "interrupted")
        self.assertEqual(reporter.last_message, "Cancellation requested")
        reporter.event.assert_called_with("interrupted", message="Cancellation requested")

        failure = self.reporter()
        suppress = failure.__exit__(RuntimeError, RuntimeError("ClickHouse unavailable"), None)
        self.assertFalse(suppress)
        self.assertEqual(failure.stage, "failed")
        self.assertEqual(failure.last_message, "ClickHouse unavailable")

    def test_keyboard_interrupt_is_suppressed_for_exit_code_translation(self) -> None:
        reporter = self.reporter()
        suppress = reporter.__exit__(KeyboardInterrupt, KeyboardInterrupt(), None)
        self.assertTrue(suppress)
        self.assertTrue(reporter.was_interrupted)
        self.assertEqual(reporter.stage, "interrupted")


class TemporalContractTest(unittest.TestCase):
    def _five_seconds(self) -> BarView:
        features = torch.zeros((5, len(FEATURE_NAMES)), dtype=torch.float32)
        features[:, FEATURE_INDEX["trade_present"]] = 1
        features[:, FEATURE_INDEX["trade_open"]] = torch.arange(10, 15)
        features[:, FEATURE_INDEX["trade_high"]] = torch.arange(11, 16)
        features[:, FEATURE_INDEX["trade_low"]] = torch.arange(9, 14)
        features[:, FEATURE_INDEX["trade_close"]] = torch.arange(10.5, 15.5)
        features[:, FEATURE_INDEX["trade_size_sum"]] = 2
        features[:, FEATURE_INDEX["trade_event_count"]] = 1
        starts = torch.arange(5, dtype=torch.long) * 1_000_000
        ends = starts + 1_000_000
        return BarView(features, starts, ends, ends)

    def test_coarse_bar_is_unavailable_until_its_close(self) -> None:
        base = self._five_seconds()
        coarse = rollup_intraday_view(base, 5_000_000)
        self.assertEqual(coarse.features.shape[0], 1)
        indices = causal_asof_indices(coarse.available_at_us, base.available_at_us)
        self.assertEqual(indices.tolist(), [-1, -1, -1, -1, 0])
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_open"]]), 10.0)
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_close"]]), 14.5)
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_size_sum"]]), 10.0)

    def test_three_sessions_collapse_to_one_daily_bar_available_at_20_et(self) -> None:
        rows = []
        bounds = ((1, 2), (2, 3), (3, 4))
        for session, (start, end) in zip(("premarket", "regular", "after_hours"), bounds, strict=True):
            row = {name: 0.0 for name in FEATURE_NAMES}
            row.update(
                local_date="2026-01-02",
                ticker="AAPL",
                session_kind=session,
                bar_start_us=start,
                bar_end_us=end,
                available_at_us=end,
            )
            rows.append(row)
        rows[1]["trade_present"] = 1.0
        rows[1]["trade_open"] = 100.0
        rows[1]["trade_high"] = 102.0
        rows[1]["trade_low"] = 99.0
        rows[1]["trade_close"] = 101.0
        rows[1]["trade_size_sum"] = 10.0
        rows[1]["trade_event_count"] = 2.0
        rows[1]["source_event_count"] = 2.0
        dates, daily = daily_session_frame_to_view(pl.DataFrame(rows))
        self.assertEqual(dates, ["2026-01-02"])
        self.assertEqual(daily.available_at_us.tolist(), [4])
        self.assertEqual(float(daily.features[0, FEATURE_INDEX["trade_close"]]), 101.0)
        self.assertEqual(float(daily.features[0, FEATURE_INDEX["source_event_count"]]), 2.0)
        with self.assertRaisesRegex(ValueError, "premarket, regular, and after-hours"):
            daily_session_frame_to_view(pl.DataFrame(rows[:2]))

    def test_horizon_support_is_indexed_without_window_copies(self) -> None:
        timestamps = torch.arange(1, 11, dtype=torch.long) * 1_000_000
        indices, mask = horizon_target_indices(
            timestamps,
            torch.tensor([2_000_000, 8_000_000]),
            torch.tensor([1_000_000, 2_000_000]),
        )
        self.assertEqual(indices.tolist(), [[2, 3], [8, 9]])
        self.assertEqual(mask.tolist(), [[True, True], [True, True]])

        raw = self._five_seconds().features
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([0, 1, 3]),
            torch.tensor([1_000_000, 2_000_000]),
            condition_flags=torch.tensor(
                [[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 1, 0], [0, 0, 0, 0], [0, 1, 0, 1]],
                dtype=torch.float32,
            ),
        )
        self.assertEqual(targets.values.shape, (3, 2, len(TARGET_NAMES)))
        self.assertEqual(targets.values[0, 2 - 1, -4:].tolist(), [1.0, 0.0, 1.0, 0.0])
        self.assertFalse(bool(targets.mask[2, 1].any()))

    def test_physical_returns_require_new_same_family_updates(self) -> None:
        raw = self._five_seconds().features.clone()
        for family, offset in (("bid", 0.0), ("ask", 0.1)):
            raw[:, FEATURE_INDEX[f"{family}_present"]] = 1
            raw[:, FEATURE_INDEX[f"{family}_open"]] = torch.arange(9.8 + offset, 14.8 + offset)
            raw[:, FEATURE_INDEX[f"{family}_high"]] = torch.arange(10.2 + offset, 15.2 + offset)
            raw[:, FEATURE_INDEX[f"{family}_low"]] = torch.arange(9.7 + offset, 14.7 + offset)
            raw[:, FEATURE_INDEX[f"{family}_close"]] = torch.arange(10.0 + offset, 15.0 + offset)
        raw[1:3, FEATURE_INDEX["ask_present"]] = 0
        raw[1:3, FEATURE_INDEX["ask_close"]] = 0
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([0]),
            torch.tensor([2_000_000]),
            available_at_us=torch.arange(1, 6, dtype=torch.long) * 1_000_000,
            coverage_end_us=5_000_000,
        )
        bid_close = TARGET_NAMES.index("bid_close_return")
        ask_close = TARGET_NAMES.index("ask_close_return")
        trade_close = TARGET_NAMES.index("trade_close_return")
        self.assertTrue(bool(targets.mask[0, 0, bid_close]))  # bid update
        self.assertFalse(bool(targets.mask[0, 0, ask_close]))  # no ask update
        self.assertTrue(bool(targets.mask[0, 0, trade_close]))  # trade update
        self.assertEqual(float(targets.values[0, 0, ask_close]), 0.0)

    def test_ohlc_targets_preserve_signed_high_and_low_returns(self) -> None:
        raw = self._five_seconds().features
        physical = build_physical_horizon_targets(
            raw,
            torch.tensor([0]),
            torch.tensor([2_000_000]),
            available_at_us=torch.arange(1, 6, dtype=torch.long) * 1_000_000,
            coverage_end_us=5_000_000,
        )
        high = TARGET_NAMES.index("trade_high_return")
        low = TARGET_NAMES.index("trade_low_return")
        self.assertTrue(bool(physical.mask[0, 0, high]))
        self.assertGreater(float(physical.values[0, 0, high]), 0.0)
        self.assertLess(float(physical.values[0, 0, low]), 0.0)

        autoregressive = build_next_bar_targets(raw)
        self.assertGreater(float(autoregressive.values[0, high]), 0.0)
        self.assertLess(float(autoregressive.values[0, low]), 0.0)

    def test_trade_target_masks_follow_field_condition_eligibility(self) -> None:
        raw = self._five_seconds().features.clone()
        raw[1, FEATURE_INDEX["trade_close"]] = 0.0
        raw[1, FEATURE_INDEX["trade_open"]] = 0.0
        physical = build_physical_horizon_targets(
            raw,
            torch.tensor([0]),
            torch.tensor([1_000_000]),
            available_at_us=torch.arange(1, 6, dtype=torch.long) * 1_000_000,
            coverage_end_us=5_000_000,
        )
        high = TARGET_NAMES.index("trade_high_return")
        close = TARGET_NAMES.index("trade_close_return")
        self.assertTrue(bool(physical.mask[0, 0, high]))
        self.assertFalse(bool(physical.mask[0, 0, close]))
        autoregressive = build_next_bar_targets(raw)
        self.assertTrue(bool(autoregressive.mask[0, high]))
        self.assertFalse(bool(autoregressive.mask[0, close]))

    def test_volume_only_trade_size_does_not_contaminate_vwap_price(self) -> None:
        raw = self._five_seconds().features[:1].clone()
        raw[0, FEATURE_INDEX["trade_close"]] = 100.0
        raw[0, FEATURE_INDEX["trade_size_sum"]] = 110.0
        raw[0, FEATURE_INDEX["trade_price_size_sum"]] = 1_000.0
        raw[0, FEATURE_INDEX["trade_price_eligible_size_sum"]] = 10.0
        projected = project_stationary_features(raw)
        vwap = MODEL_FEATURE_NAMES.index("trade_vwap_deviation_bps")
        self.assertAlmostEqual(float(projected[0, vwap]), 0.0, places=6)

    def test_sparse_storage_densifies_without_fabricating_families(self) -> None:
        base = self._five_seconds()
        sparse = BarView(
            features=base.features[[0, 2, 4]],
            bar_start_us=base.bar_start_us[[0, 2, 4]],
            bar_end_us=base.bar_end_us[[0, 2, 4]],
            available_at_us=base.available_at_us[[0, 2, 4]],
        )
        dense = densify_one_second_view(sparse)
        self.assertEqual(dense.features.shape[0], 5)
        self.assertEqual(dense.features[:, FEATURE_INDEX["trade_present"]].tolist(), [1, 0, 1, 0, 1])
        self.assertEqual(dense.available_at_us.tolist(), [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000])

        full_clock = densify_one_second_view(sparse, clock_start_us=0, clock_end_us=6_000_000)
        self.assertEqual(full_clock.features.shape[0], 6)
        self.assertEqual(full_clock.features[:, FEATURE_INDEX["trade_present"]].tolist(), [1, 0, 1, 0, 1, 0])


class ModelContractTest(unittest.TestCase):
    def test_fused_linear_group_matches_independent_outputs_and_gradients(self) -> None:
        torch.manual_seed(41)
        actual_layers = torch.nn.ModuleList((
            torch.nn.Linear(7, 5, bias=False),
            torch.nn.Linear(7, 3, bias=True),
            torch.nn.Linear(7, 9, bias=True),
        ))
        reference_layers = deepcopy(actual_layers)
        actual_input = torch.randn(2, 4, 7, requires_grad=True)
        reference_input = actual_input.detach().clone().requires_grad_(True)

        actual_outputs = _fused_linear_group(actual_input, *actual_layers)
        reference_outputs = tuple(layer(reference_input) for layer in reference_layers)
        for actual, reference in zip(actual_outputs, reference_outputs, strict=True):
            torch.testing.assert_close(actual, reference, atol=2e-6, rtol=2e-6)

        sum(output.square().sum() for output in actual_outputs).backward()
        sum(output.square().sum() for output in reference_outputs).backward()
        torch.testing.assert_close(actual_input.grad, reference_input.grad, atol=2e-5, rtol=2e-5)
        for actual, reference in zip(actual_layers, reference_layers, strict=True):
            torch.testing.assert_close(actual.weight.grad, reference.weight.grad, atol=2e-5, rtol=2e-5)
            if actual.bias is not None and reference.bias is not None:
                torch.testing.assert_close(actual.bias.grad, reference.bias.grad, atol=2e-5, rtol=2e-5)

    def test_fused_linear_group_preserves_autocast_dtype_with_biases(self) -> None:
        layers = (
            torch.nn.Linear(8, 3, bias=False),
            torch.nn.Linear(8, 4, bias=True),
        )
        value = torch.randn(2, 8, requires_grad=True)
        with torch.autocast("cpu", dtype=torch.bfloat16):
            outputs = _fused_linear_group(value, *layers)
        self.assertEqual({output.dtype for output in outputs}, {torch.bfloat16})
        sum(output.float().square().sum() for output in outputs).backward()
        self.assertTrue(bool(torch.isfinite(value.grad).all()))

    def test_precomputed_attention_mask_matches_per_call_mask_and_gradients(self) -> None:
        torch.manual_seed(43)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=32, n_layers=1,
            n_heads=4, n_kv_heads=2, horizon_rank=8, dropout=0.0,
        )
        attention = CausalSelfAttention(config).eval()
        actual_input = torch.randn(2, 9, 32, requires_grad=True)
        reference_input = actual_input.detach().clone().requires_grad_(True)
        token_mask = torch.tensor([
            [False, False, True, True, True, True, True, True, True],
            [False, True, True, True, True, True, True, True, True],
        ])
        shared_mask = attention.build_attention_mask(
            9, attention_window=4, device=actual_input.device, token_mask=token_mask,
        )
        actual = attention(
            actual_input, attention_window=4, token_mask=token_mask,
            attention_mask=shared_mask,
        )
        reference = attention(
            reference_input, attention_window=4, token_mask=token_mask,
        )
        torch.testing.assert_close(actual, reference, atol=2e-6, rtol=2e-6)
        actual_gradient = torch.autograd.grad(actual.square().sum(), actual_input)[0]
        reference_gradient = torch.autograd.grad(reference.square().sum(), reference_input)[0]
        torch.testing.assert_close(actual_gradient, reference_gradient, atol=2e-5, rtol=2e-5)

    def test_encoder_builds_each_unique_layer_mask_once(self) -> None:
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=32, n_layers=4,
            n_heads=4, n_kv_heads=2, horizon_rank=8, dropout=0.0,
        )
        model = BarGPTV2(config).eval()
        features = torch.randn(2, 13, len(MODEL_FEATURE_NAMES))
        token_mask = torch.tensor([
            [False, *([True] * 12)],
            [False, False, *([True] * 11)],
        ])
        original = CausalSelfAttention.build_attention_mask
        with patch.object(
            CausalSelfAttention, "build_attention_mask", side_effect=original,
        ) as build_mask:
            model.encode(
                features, 1_000_000, 0, attention_window=10,
                token_mask=token_mask,
            )
        # Radius nine across four layers produces windows 4, 3, 3, 3.
        self.assertEqual(build_mask.call_count, 2)

    def test_fused_execution_preserves_legacy_parameter_keys(self) -> None:
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=32, n_layers=2,
            n_heads=4, n_kv_heads=2, horizon_rank=8,
        )
        model = BarGPTV2(config)
        state = model.state_dict()
        self.assertIn("blocks.0.attention.q_proj.weight", state)
        self.assertIn("blocks.0.attention.k_proj.weight", state)
        self.assertIn("blocks.0.attention.v_proj.weight", state)
        self.assertIn("blocks.0.ffn.gate.weight", state)
        self.assertIn("blocks.0.ffn.up.weight", state)
        self.assertFalse(any("qkv_proj" in key or "gate_up_proj" in key for key in state))
        BarGPTV2(config).load_state_dict(state, strict=True)

    def test_full_fused_model_matches_independent_projection_reference(self) -> None:
        torch.manual_seed(47)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=32, n_layers=2,
            n_heads=4, n_kv_heads=2, horizon_rank=8, dropout=0.0,
        )
        actual_model = BarGPTV2(config).eval()
        reference_model = deepcopy(actual_model)
        actual_views = {
            "1s": torch.randn(2, 7, len(MODEL_FEATURE_NAMES), requires_grad=True),
            "5s": torch.randn(2, 4, len(MODEL_FEATURE_NAMES), requires_grad=True),
        }
        reference_views = {
            name: value.detach().clone().requires_grad_(True)
            for name, value in actual_views.items()
        }
        kwargs = {
            "timeframe_us": {"1s": 1_000_000, "5s": 5_000_000},
            "pathway_ids": {"1s": 0, "5s": 1},
            "base_view": "1s",
            "origin_indices": torch.tensor([[2, 4, 6], [1, 3, 5]]),
            "asof_indices": {"5s": torch.tensor([[-1, 1, 3], [0, 2, 3]])},
            "view_masks": {
                "1s": torch.tensor([
                    [False, True, True, True, True, True, True],
                    [True, True, True, True, True, True, True],
                ]),
                "5s": torch.tensor([
                    [False, True, True, True],
                    [True, True, True, True],
                ]),
            },
            "attention_windows": {"1s": 5, "5s": 3},
            "horizon_ids": torch.tensor([0, 1]),
        }

        def independent(
            value: torch.Tensor,
            *layers: torch.nn.Linear,
            fused_weight: torch.Tensor | None = None,
            fused_bias: torch.Tensor | None = None,
        ):
            del fused_weight, fused_bias
            return tuple(layer(value) for layer in layers)

        actual = actual_model(actual_views, **kwargs)
        with patch(
            "research.bar_gpt.v2.model._fused_linear_group",
            side_effect=independent,
        ):
            reference = reference_model(reference_views, **kwargs)

        def tensors(output):
            values = [output.embeddings]
            values.extend(output.scale_embeddings.values())
            values.extend(output.autoregressive.values())
            values.extend(output.autoregressive_return_class_logits.values())
            values.extend((
                output.horizon_quantiles,
                output.horizon_availability_logits,
                output.horizon_return_class_logits,
            ))
            return tuple(value for value in values if value is not None)

        actual_tensors = tensors(actual)
        reference_tensors = tensors(reference)
        for actual_tensor, reference_tensor in zip(
            actual_tensors, reference_tensors, strict=True,
        ):
            torch.testing.assert_close(
                actual_tensor, reference_tensor, atol=3e-5, rtol=3e-5,
            )
        sum(value.float().square().mean() for value in actual_tensors).backward()
        sum(value.float().square().mean() for value in reference_tensors).backward()
        for name in actual_views:
            torch.testing.assert_close(
                actual_views[name].grad,
                reference_views[name].grad,
                atol=5e-5,
                rtol=5e-5,
            )
        for (actual_name, actual_parameter), (reference_name, reference_parameter) in zip(
            actual_model.named_parameters(), reference_model.named_parameters(), strict=True,
        ):
            self.assertEqual(actual_name, reference_name)
            torch.testing.assert_close(
                actual_parameter.grad,
                reference_parameter.grad,
                atol=5e-5,
                rtol=5e-5,
            )

    def test_masked_encoder_matches_independent_sequences_and_gradients(self) -> None:
        torch.manual_seed(37)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=32, n_layers=2,
            n_heads=4, n_kv_heads=2, horizon_rank=8, dropout=0.0,
        )
        model = BarGPTV2(config).eval()
        actual_input = torch.randn(
            2, 11, len(MODEL_FEATURE_NAMES), requires_grad=True
        )
        reference_input = actual_input.detach().clone().requires_grad_(True)
        mask = torch.tensor([
            [False, False, True, True, True, True, True, True, True, True, False],
            [True, True, True, True, True, False, False, False, False, False, False],
        ])
        actual = model.encode(
            actual_input, 1_000_000, 0, attention_window=7,
            token_mask=mask,
        )
        first = model.encode(
            reference_input[0:1, 2:10], 1_000_000, 0, attention_window=7
        )
        second = model.encode(
            reference_input[1:2, :5], 1_000_000, 0, attention_window=7
        )
        reference = torch.zeros_like(actual)
        reference[0:1, 2:10] = first
        reference[1:2, :5] = second
        torch.testing.assert_close(actual, reference, atol=3e-5, rtol=3e-5)

        actual.square().sum().backward()
        reference.square().sum().backward()
        torch.testing.assert_close(
            actual_input.grad, reference_input.grad, atol=5e-5, rtol=5e-5
        )

    def test_forward_shapes_and_future_causality(self) -> None:
        torch.manual_seed(7)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES),
            d_model=64,
            n_layers=2,
            n_heads=4,
            n_kv_heads=2,
            horizon_rank=16,
            dropout=0.0,
        )
        model = BarGPTV2(config).eval()
        fine = torch.randn(1, 8, len(MODEL_FEATURE_NAMES))
        coarse = torch.randn(1, 2, len(MODEL_FEATURE_NAMES))
        origins = torch.tensor([[0, 1, 2, 3]])
        coarse_asof = torch.tensor([[-1, -1, -1, 0]])
        kwargs = {
            "timeframe_us": {"1s": 1_000_000, "5s": 5_000_000},
            "pathway_ids": {"1s": 0, "5s": 0},
            "base_view": "1s",
            "origin_indices": origins,
            "asof_indices": {"5s": coarse_asof},
            "horizon_ids": torch.tensor([0, 1, 2]),
        }
        first = model({"1s": fine, "5s": coarse}, **kwargs)
        changed = fine.clone()
        changed[:, 5:] += 100
        second = model({"1s": changed, "5s": coarse}, **kwargs)
        self.assertEqual(first.embeddings.shape, (1, 4, 64))
        self.assertEqual(first.horizon_quantiles.shape, (1, 4, 3, CONTINUOUS_TARGET_COUNT, 3))
        self.assertEqual(first.horizon_availability_logits.shape, (1, 4, 3, AVAILABILITY_TARGET_COUNT))
        self.assertTrue(torch.all(first.horizon_quantiles[..., 1:] >= first.horizon_quantiles[..., :-1]))
        torch.testing.assert_close(first.embeddings, second.embeddings)

    def test_continuous_timeframe_value_accepts_unseen_scale(self) -> None:
        config = BarGPTConfig(d_model=64, n_layers=1, n_heads=4, n_kv_heads=2, horizon_rank=16)
        model = BarGPTV2(config).eval()
        features = torch.randn(1, 5, len(MODEL_FEATURE_NAMES))
        output = model(
            {"custom": features},
            timeframe_us={"custom": 12_000_000},
            pathway_ids={"custom": 0},
            base_view="custom",
            origin_indices=torch.tensor([[1, 2]]),
            horizon_ids=torch.tensor([0]),
        )
        self.assertEqual(output.embeddings.shape, (1, 2, 64))

    def test_stationary_projection_has_no_absolute_price_channel(self) -> None:
        raw = torch.zeros((4, len(FEATURE_NAMES)))
        for prefix in ("trade", "bid", "ask"):
            raw[:, FEATURE_INDEX[f"{prefix}_present"]] = 1
            for field in ("open", "high", "low", "close"):
                raw[:, FEATURE_INDEX[f"{prefix}_{field}"]] = 100.0
            raw[:, FEATURE_INDEX[f"{prefix}_size_sum"]] = 10
            raw[:, FEATURE_INDEX[f"{prefix}_size_squared_sum"]] = 100
            raw[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] = 1_000
            raw[:, FEATURE_INDEX[f"{prefix}_event_count"]] = 1
        projected = project_stationary_features(raw)
        scaled = raw.clone()
        for prefix in ("trade", "bid", "ask"):
            for field in ("open", "high", "low", "close"):
                scaled[:, FEATURE_INDEX[f"{prefix}_{field}"]] *= 5
            scaled[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] *= 5
        torch.testing.assert_close(projected, project_stationary_features(scaled))

    def test_stationary_projection_preserves_signed_low_return(self) -> None:
        raw = torch.zeros((1, len(FEATURE_NAMES)))
        raw[:, FEATURE_INDEX["trade_present"]] = 1
        raw[:, FEATURE_INDEX["trade_open"]] = 100.0
        raw[:, FEATURE_INDEX["trade_high"]] = 103.0
        raw[:, FEATURE_INDEX["trade_low"]] = 97.0
        raw[:, FEATURE_INDEX["trade_close"]] = 101.0
        projected = project_stationary_features(raw)
        self.assertGreater(
            float(projected[0, MODEL_FEATURE_NAMES.index("trade_high_from_open_return")]), 0.0
        )
        self.assertLess(
            float(projected[0, MODEL_FEATURE_NAMES.index("trade_low_from_open_return")]), 0.0
        )

    def test_daily_rows_without_intraday_moments_remain_neutral(self) -> None:
        raw = torch.zeros((2, len(FEATURE_NAMES)))
        raw[:, FEATURE_INDEX["trade_present"]] = 1
        for field in ("open", "high", "low", "close"):
            raw[:, FEATURE_INDEX[f"trade_{field}"]] = 100
        raw[:, FEATURE_INDEX["trade_size_sum"]] = 1000
        raw[:, FEATURE_INDEX["trade_event_count"]] = 10
        projected = project_stationary_features(raw)
        self.assertEqual(float(projected[1, MODEL_FEATURE_NAMES.index("trade_vwap_deviation_bps")]), 0.0)
        self.assertEqual(float(projected[1, MODEL_FEATURE_NAMES.index("trade_size_cv")]), 0.0)


if __name__ == "__main__":
    unittest.main()
