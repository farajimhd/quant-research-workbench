from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from datetime import date

import numpy as np
import pyarrow as pa

from src.backend.backtest_ladder_coordinator import (
    PreparedLadderCampaign, PreparedLadderProposal, completed_cross_indices,
    submit_ladder_boundary)


class LadderCompactPreparationTests(unittest.TestCase):
    def test_crosses_require_two_completed_adjacent_prices_and_current_admission(self):
        source = pa.table({'close_int': [100, 105, 100, 105, 100, 105, 100, 105],
            'price_valid': [1, 1, 1, 1, 1, 1, 1, 1]})
        gate = SimpleNamespace(boundary_ms=np.array([100, 200, 300, 500, 600, 700, 800, 900]),
            market_rejection=np.array([0, 0, 0, 0, 0, 1, 0, 0]),
            admission_boundary_ms=np.array([100, 100, 100, 100, 100, 100, 800, 800]))
        setup = SimpleNamespace(reason='setup_qualified', source_row_index=0,
            resistance=SimpleNamespace(upper_comparison_int=100), admission_boundary_ms=100)
        result = completed_cross_indices(gate, source, setup,
            tick_int=1, break_buffer_ticks=1)
        self.assertEqual(result.tolist(), [1])

    def test_break_buffer_and_missing_price_cannot_create_a_cross(self):
        gate = SimpleNamespace(boundary_ms=np.array([100, 200, 300, 400]),
            market_rejection=np.zeros(4, dtype=np.uint8),
            admission_boundary_ms=np.full(4, 100))
        setup = SimpleNamespace(reason='setup_qualified', source_row_index=0,
            resistance=SimpleNamespace(upper_comparison_int=100), admission_boundary_ms=100)
        source = pa.table({'close_int': [100, 101, 100, 102], 'price_valid': [1, 1, 0, 1]})
        self.assertEqual(completed_cross_indices(gate, source, setup,
            tick_int=1, break_buffer_ticks=1).tolist(), [])

    def test_compact_boundary_lookup_keeps_simultaneous_proposals(self):
        def proposal(clock, ticker):
            return PreparedLadderProposal(SimpleNamespace(boundary_ms=clock,
                setup=SimpleNamespace(ticker=ticker)), 1000.)
        campaign = PreparedLadderCampaign((proposal(100, 'A'), proposal(100, 'B'),
            proposal(200, 'A')), ())
        self.assertEqual([row.ticker for row in campaign.at(100)], ['A', 'B'])
        self.assertEqual(campaign.at(150), ())
        with self.assertRaisesRegex(ValueError, 'backward'):
            PreparedLadderCampaign((proposal(200, 'A'), proposal(100, 'A')), ())


class LadderSequentialAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_strategy_accepts_typed_session_exit_and_rejects_raw_source(self):
        from tests.test_numbered_session_exit_reason_authority import runtime as make_runtime, source
        from src.trading_runtime.signals import StrategyEvaluation
        runtime, intent = make_runtime(49), source(49)
        with self.assertRaisesRegex(ValueError, 'exclusive native typed source'):
            await runtime._execute_intents(StrategyEvaluation(intents=(intent,)), 'DU1', None)
        result = await runtime._execute_intents(StrategyEvaluation(intents=(intent,)),
            'DU1', None, numbered_exit_assignment_id='A1')
        self.assertEqual(result, [{'decision': {'status': 'fixture_declined'}, 'order_group': None}])
        records = runtime.journal.records(runtime.run_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(runtime.journal.numbered_session_exit_for_record(records[0].record_id), intent)

    async def test_declared_empty_terminal_clock_does_not_create_market_data_or_fills(self):
        from dataclasses import replace
        from tests.test_squeeze_ladder_automatic import runtime_fixture
        from src.backend.backtest_market_data import market_day_boundary
        runtime, _, _, assignment, _, _ = await runtime_fixture()
        try:
            runtime.config = replace(runtime.config, strategy_id='early-squeeze-strategy', strategy_revision=49)
            runtime.order_manager.strategy_id = runtime.config.strategy_id
            runtime.order_manager.strategy_revision = runtime.config.strategy_revision
            before = await runtime.broker.account_summary('DU1')
            quote = runtime.execution_market_data.snapshot(assignment.ticker)
            await runtime.advance_numbered_session_clock(19_800_000)
            self.assertEqual(runtime.last_event_time,
                market_day_boundary(runtime.config.anchor_date, 19_800_000))
            self.assertEqual(await runtime.broker.account_summary('DU1'), before)
            self.assertEqual(runtime.execution_market_data.snapshot(assignment.ticker), quote)
            self.assertEqual(runtime.broker.financially_active_tickers(), ())
            with self.assertRaisesRegex(ValueError, 'backward'):
                await runtime.advance_numbered_session_clock(19_740_000)
        finally:
            await runtime.order_manager.close()
            runtime.journal.close()

    async def test_cash_snapshot_refreshes_between_ranked_simultaneous_requests(self):
        from src.backend.backtest_market_data import market_day_boundary
        day, clock = date(2026, 8, 26), 100
        runtime = SimpleNamespace(last_event_time=market_day_boundary(day, clock))
        owners = [SimpleNamespace(ticker='A', conid=20, account_id='account', assignment_id='A'),
                  SimpleNamespace(ticker='B', conid=10, account_id='account', assignment_id='B')]
        decisions = [SimpleNamespace(boundary_ms=clock, setup=SimpleNamespace(ticker=ticker))
            for ticker in ('A', 'B')]
        campaign = PreparedLadderCampaign(tuple(PreparedLadderProposal(row, 1000.)
            for row in decisions), ())
        authority = SimpleNamespace(session_date=day, policy='policy', context_for=lambda ticker: ticker)
        events, cash = [], [10000]
        async def capture():
            events.append(('snapshot', cash[0]))
        async def submit(_runtime, decision, **kwargs):
            events.append(('request', decision.setup.ticker, cash[0]))
            cash[0] -= 3000
            return []
        with patch('src.trading_runtime.squeeze_ladder_automatic.submit_automatic_ladder',
                new=AsyncMock(side_effect=submit)):
            await submit_ladder_boundary(runtime, campaign, boundary_ms=clock,
                assignments=owners, source_authority=authority, before_acquisition=capture)
        self.assertEqual(events, [('snapshot', 10000), ('request', 'B', 10000),
            ('snapshot', 7000), ('request', 'A', 7000)])
