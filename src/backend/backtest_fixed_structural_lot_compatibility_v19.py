"""Exact reviewed selected-exit successor restoration to source 2c37bd270b52f51e8feddfad1aa54a1901e7fe81."""

import ast
from hashlib import sha256

REVIEWED_PARENT_DELTAS = {'src/backend/backtest_fixed_structural_lot_configuration.py': ('b4e996f671d4a5071da91afceabd520b74bae654015933a745479a3dea2ed821',
                                                                '4dc8b588b5f83be1aa0a256c3350081a85fb1d3f485eca9e39a7dc021ad8e9f4',
                                                                (('        '
                                                                  "expected.add('empty_protection_confirmation_policy')\n"
                                                                  '    from '
                                                                  'src.trading_runtime.complete_market_window_policy '
                                                                  'import RULE as WINDOW_RULE, '
                                                                  'parse_complete_market_window_policy\n'
                                                                  '    window_selected = '
                                                                  'WINDOW_RULE in '
                                                                  'contract.release.rule_set_contracts\n'
                                                                  '    if window_selected:\n'
                                                                  '        '
                                                                  "expected.add('complete_market_window_policy')\n"
                                                                  '    from '
                                                                  'src.trading_runtime.selected_exit_publication_policy '
                                                                  'import RULE as EXIT_RULE, '
                                                                  'parse_selected_exit_publication_policy\n'
                                                                  '    exit_selected = EXIT_RULE '
                                                                  'in '
                                                                  'contract.release.rule_set_contracts\n'
                                                                  '    if exit_selected:\n'
                                                                  '        '
                                                                  "expected.add('selected_exit_publication_policy')\n"
                                                                  '    if type(params) is not dict '
                                                                  'or set(params) != expected:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot parameter "
                                                                  "companions differ')\n"
                                                                  '    if reuse_selected and '
                                                                  "parse_packet_validation_reuse_policy(params['packet_validation_reuse_policy']) "
                                                                  '!= '
                                                                  'contract.validation_reuse_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot "
                                                                  'validation reuse bounds differ '
                                                                  "from registered factory')\n"
                                                                  '    if projection_selected and '
                                                                  "parse_projected_configuration_reuse_policy(params['projected_configuration_reuse_policy']) "
                                                                  '!= '
                                                                  'contract.projection_reuse_policy:\n',
                                                                  '        '
                                                                  "expected.add('empty_protection_confirmation_policy')\n"
                                                                  '    from '
                                                                  'src.trading_runtime.complete_market_window_policy '
                                                                  'import RULE as WINDOW_RULE, '
                                                                  'parse_complete_market_window_policy\n'
                                                                  '    window_selected = '
                                                                  'WINDOW_RULE in '
                                                                  'contract.release.rule_set_contracts\n'
                                                                  '    if window_selected:\n'
                                                                  '        '
                                                                  "expected.add('complete_market_window_policy')\n"
                                                                  '    if type(params) is not dict '
                                                                  'or set(params) != expected:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot parameter "
                                                                  "companions differ')\n"
                                                                  '    if reuse_selected and '
                                                                  "parse_packet_validation_reuse_policy(params['packet_validation_reuse_policy']) "
                                                                  '!= '
                                                                  'contract.validation_reuse_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot "
                                                                  'validation reuse bounds differ '
                                                                  "from registered factory')\n"
                                                                  '    if projection_selected and '
                                                                  "parse_projected_configuration_reuse_policy(params['projected_configuration_reuse_policy']) "
                                                                  '!= '
                                                                  'contract.projection_reuse_policy:\n'),
                                                                 ("        raise ValueError('Owned "
                                                                  'snapshot bounds differ from '
                                                                  "registered factory')\n"
                                                                  '    if empty_selected and '
                                                                  "parse_empty_protection_confirmation_policy(params['empty_protection_confirmation_policy']) "
                                                                  '!= '
                                                                  'contract.empty_confirmation_policy:\n'
                                                                  "        raise ValueError('Empty "
                                                                  'confirmation differs from '
                                                                  "registered factory')\n"
                                                                  '    if window_selected and '
                                                                  "parse_complete_market_window_policy(params['complete_market_window_policy']) "
                                                                  '!= '
                                                                  'contract.complete_market_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Complete market "
                                                                  'window bounds differ from '
                                                                  "registered factory')\n"
                                                                  '    if exit_selected and '
                                                                  "parse_selected_exit_publication_policy(params['selected_exit_publication_policy']) "
                                                                  '!= '
                                                                  'contract.selected_exit_publication_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Selected exit "
                                                                  'publication differs from '
                                                                  "registered factory')\n"
                                                                  '    if '
                                                                  "parse_fixed_structural_lot_policy(params['fixed_structural_lot_policy']) "
                                                                  '!= '
                                                                  'contract.fixed_structural_lot_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot policy "
                                                                  'differs from exact registered '
                                                                  "factory')\n"
                                                                  '    manifest = '
                                                                  "strategy.get('numbered_release')\n"
                                                                  '    if type(manifest) is not '
                                                                  'dict:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot approved "
                                                                  "manifest required')\n",
                                                                  "        raise ValueError('Owned "
                                                                  'snapshot bounds differ from '
                                                                  "registered factory')\n"
                                                                  '    if empty_selected and '
                                                                  "parse_empty_protection_confirmation_policy(params['empty_protection_confirmation_policy']) "
                                                                  '!= '
                                                                  'contract.empty_confirmation_policy:\n'
                                                                  "        raise ValueError('Empty "
                                                                  'confirmation differs from '
                                                                  "registered factory')\n"
                                                                  '    if window_selected and '
                                                                  "parse_complete_market_window_policy(params['complete_market_window_policy']) "
                                                                  '!= '
                                                                  'contract.complete_market_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Complete market "
                                                                  'window bounds differ from '
                                                                  "registered factory')\n"
                                                                  '    if '
                                                                  "parse_fixed_structural_lot_policy(params['fixed_structural_lot_policy']) "
                                                                  '!= '
                                                                  'contract.fixed_structural_lot_policy:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot policy "
                                                                  'differs from exact registered '
                                                                  "factory')\n"
                                                                  '    manifest = '
                                                                  "strategy.get('numbered_release')\n"
                                                                  '    if type(manifest) is not '
                                                                  'dict:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot approved "
                                                                  "manifest required')\n"),
                                                                 ('        from '
                                                                  'src.trading_runtime.fixed_structural_lot_release_v17 '
                                                                  'import '
                                                                  'derive_fixed_structural_lot_release\n'
                                                                  '    from '
                                                                  'src.trading_runtime.complete_market_window_policy '
                                                                  'import RULE as WINDOW_RULE\n'
                                                                  '    window_selected = '
                                                                  'WINDOW_RULE in '
                                                                  'numbered_strategy(number).rule_set_contracts\n'
                                                                  '    if window_selected:\n'
                                                                  '        from '
                                                                  'src.trading_runtime.fixed_structural_lot_release_v18 '
                                                                  'import '
                                                                  'derive_fixed_structural_lot_release\n'
                                                                  '    from '
                                                                  'src.trading_runtime.selected_exit_publication_policy '
                                                                  'import RULE as EXIT_RULE\n'
                                                                  '    exit_selected = EXIT_RULE '
                                                                  'in '
                                                                  'numbered_strategy(number).rule_set_contracts\n'
                                                                  '    if exit_selected:\n'
                                                                  '        from '
                                                                  'src.trading_runtime.fixed_structural_lot_release_v19 '
                                                                  'import '
                                                                  'derive_fixed_structural_lot_release\n'
                                                                  '    contract = '
                                                                  'declared_fixed_structural_lot_contract(number)\n'
                                                                  '    if contract is None or '
                                                                  'parent.strategy_number != '
                                                                  'numbered_strategy_parent(number):\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot "
                                                                  "registered parent differs')\n"
                                                                  '    return '
                                                                  'derive_fixed_structural_lot_release(parent,\n'
                                                                  '        '
                                                                  'parent_release=numbered_strategy(parent.strategy_number), '
                                                                  'release=contract.release,\n',
                                                                  '        from '
                                                                  'src.trading_runtime.fixed_structural_lot_release_v17 '
                                                                  'import '
                                                                  'derive_fixed_structural_lot_release\n'
                                                                  '    from '
                                                                  'src.trading_runtime.complete_market_window_policy '
                                                                  'import RULE as WINDOW_RULE\n'
                                                                  '    window_selected = '
                                                                  'WINDOW_RULE in '
                                                                  'numbered_strategy(number).rule_set_contracts\n'
                                                                  '    if window_selected:\n'
                                                                  '        from '
                                                                  'src.trading_runtime.fixed_structural_lot_release_v18 '
                                                                  'import '
                                                                  'derive_fixed_structural_lot_release\n'
                                                                  '    contract = '
                                                                  'declared_fixed_structural_lot_contract(number)\n'
                                                                  '    if contract is None or '
                                                                  'parent.strategy_number != '
                                                                  'numbered_strategy_parent(number):\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot "
                                                                  "registered parent differs')\n"
                                                                  '    return '
                                                                  'derive_fixed_structural_lot_release(parent,\n'
                                                                  '        '
                                                                  'parent_release=numbered_strategy(parent.strategy_number), '
                                                                  'release=contract.release,\n'),
                                                                 ("        **({'reuse_policy': "
                                                                  'contract.validation_reuse_policy} '
                                                                  'if reuse_selected else {}),\n'
                                                                  '        '
                                                                  "**({'projection_reuse_policy': "
                                                                  'contract.projection_reuse_policy} '
                                                                  'if projection_selected else '
                                                                  '{}),\n'
                                                                  '        '
                                                                  "**({'owned_snapshot_policy': "
                                                                  'contract.owned_snapshot_policy} '
                                                                  'if owned_selected else {}),\n'
                                                                  '        '
                                                                  "**({'empty_confirmation_policy': "
                                                                  'contract.empty_confirmation_policy} '
                                                                  'if empty_selected else {}),\n'
                                                                  '        '
                                                                  "**({'complete_market_policy': "
                                                                  'contract.complete_market_policy} '
                                                                  'if window_selected else {}),\n'
                                                                  '        '
                                                                  "**({'selected_exit_policy': "
                                                                  'contract.selected_exit_publication_policy} '
                                                                  'if exit_selected else {}),\n'
                                                                  '        '
                                                                  'approved_code_commit=approved_code_commit,\n'
                                                                  '        '
                                                                  'approved_code_fingerprint=approved_code_fingerprint, '
                                                                  'approval_reference=approval_reference)\n'
                                                                  '\n'
                                                                  '\n'
                                                                  'def '
                                                                  'compile_registered_fixed_structural_lot_configuration(parent, '
                                                                  '*, number, **approval):\n',
                                                                  "        **({'reuse_policy': "
                                                                  'contract.validation_reuse_policy} '
                                                                  'if reuse_selected else {}),\n'
                                                                  '        '
                                                                  "**({'projection_reuse_policy': "
                                                                  'contract.projection_reuse_policy} '
                                                                  'if projection_selected else '
                                                                  '{}),\n'
                                                                  '        '
                                                                  "**({'owned_snapshot_policy': "
                                                                  'contract.owned_snapshot_policy} '
                                                                  'if owned_selected else {}),\n'
                                                                  '        '
                                                                  "**({'empty_confirmation_policy': "
                                                                  'contract.empty_confirmation_policy} '
                                                                  'if empty_selected else {}),\n'
                                                                  '        '
                                                                  "**({'complete_market_policy': "
                                                                  'contract.complete_market_policy} '
                                                                  'if window_selected else {}),\n'
                                                                  '        '
                                                                  'approved_code_commit=approved_code_commit,\n'
                                                                  '        '
                                                                  'approved_code_fingerprint=approved_code_fingerprint, '
                                                                  'approval_reference=approval_reference)\n'
                                                                  '\n'
                                                                  '\n'
                                                                  'def '
                                                                  'compile_registered_fixed_structural_lot_configuration(parent, '
                                                                  '*, number, **approval):\n'))),
 'src/backend/backtest_typed_publisher.py': ('14bf8cece8fda0438d6c4a34aa34db0ce62fea9716151becc14eec2c09233a17',
                                             'a144e2dec59b86f08c4793e0b36af94d94739f6a26faf8e7db08dead8e623726',
                                             (('This adapter is intentionally not wired into '
                                               'launch until terminal recovery\n'
                                               'and the fixed-market execution path are validated '
                                               'end to end.\n'
                                               '"""\n'
                                               'from __future__ import annotations\n'
                                               'from src.trading_runtime.numbered_fixed_strategy '
                                               'import declared_fixed_rule\n'
                                               'from '
                                               'src.trading_runtime.selected_exit_publication_policy '
                                               'import requires_selected_followthrough_context\n'
                                               '\n'
                                               'import asyncio\n'
                                               'from dataclasses import dataclass, replace\n'
                                               'from datetime import date, datetime\n'
                                               'from typing import Any\n',
                                               'This adapter is intentionally not wired into '
                                               'launch until terminal recovery\n'
                                               'and the fixed-market execution path are validated '
                                               'end to end.\n'
                                               '"""\n'
                                               'from __future__ import annotations\n'
                                               'from src.trading_runtime.numbered_fixed_strategy '
                                               'import declared_fixed_rule\n'
                                               '\n'
                                               'import asyncio\n'
                                               'from dataclasses import dataclass, replace\n'
                                               'from datetime import date, datetime\n'
                                               'from typing import Any\n'),
                                              ('                               if type(unit) is '
                                               'V4FixedStructuralLotEntryBatch\n'
                                               '                               else '
                                               'self.writer.submit_automatic_ladder_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4AutomaticLadderBatch)\n'
                                               '                               else '
                                               'self.writer.submit_compound_v4(unit,\n'
                                               '                                    '
                                               "**({'first_price_source': "
                                               'self._first_price_source}\n'
                                               '                                       if '
                                               "unit.children['profit_givebacks'] or "
                                               "unit.children['confirmed_ah_failures'] or "
                                               "unit.children['liquidity_fade_failures'] or "
                                               "unit.children.get('original_risk_diagnostics') or "
                                               'requires_selected_followthrough_context(self._fixed_lot_source, '
                                               'unit) else {}))\n'
                                               '                               if isinstance(unit, '
                                               'V4CompoundBatch)\n'
                                               '                               else '
                                               'self.writer.submit_profit_exit_v4(unit,\n'
                                               '                                    '
                                               'first_price_source=self._first_price_source)\n'
                                               '                               if isinstance(unit, '
                                               'V4ProfitGivebackBatch)\n'
                                               '                               else '
                                               'self.writer.submit_confirmed_ah_exit_v4(unit,\n',
                                               '                               if type(unit) is '
                                               'V4FixedStructuralLotEntryBatch\n'
                                               '                               else '
                                               'self.writer.submit_automatic_ladder_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4AutomaticLadderBatch)\n'
                                               '                               else '
                                               'self.writer.submit_compound_v4(unit,\n'
                                               '                                    '
                                               "**({'first_price_source': "
                                               'self._first_price_source}\n'
                                               '                                       if '
                                               "unit.children['profit_givebacks'] or "
                                               "unit.children['confirmed_ah_failures'] or "
                                               "unit.children['liquidity_fade_failures'] or "
                                               "unit.children.get('original_risk_diagnostics') "
                                               'else {}))\n'
                                               '                               if isinstance(unit, '
                                               'V4CompoundBatch)\n'
                                               '                               else '
                                               'self.writer.submit_profit_exit_v4(unit,\n'
                                               '                                    '
                                               'first_price_source=self._first_price_source)\n'
                                               '                               if isinstance(unit, '
                                               'V4ProfitGivebackBatch)\n'
                                               '                               else '
                                               'self.writer.submit_confirmed_ah_exit_v4(unit,\n'),
                                              ('                               if isinstance(unit, '
                                               'V4ConfirmedAhFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_liquidity_fade_exit_v4(unit,\n'
                                               '                                    '
                                               'first_price_source=self._first_price_source)\n'
                                               '                               if isinstance(unit, '
                                               'V4LiquidityFadeFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_followthrough_exit_v4(unit,\n'
                                               '                                    '
                                               "**({'first_price_source':self._first_price_source} "
                                               'if unit.diagnostic is not None or '
                                               'requires_selected_followthrough_context(self._fixed_lot_source, '
                                               'unit) else {}))\n'
                                               '                               if isinstance(unit, '
                                               'V4FollowThroughFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_strategy_one_entry_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4StrategyOneEntryBatch)\n'
                                               '                               else '
                                               'self.writer.submit_oms_tactic_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4OmsTacticBatch)\n',
                                               '                               if isinstance(unit, '
                                               'V4ConfirmedAhFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_liquidity_fade_exit_v4(unit,\n'
                                               '                                    '
                                               'first_price_source=self._first_price_source)\n'
                                               '                               if isinstance(unit, '
                                               'V4LiquidityFadeFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_followthrough_exit_v4(unit,\n'
                                               '                                    '
                                               "**({'first_price_source':self._first_price_source} "
                                               'if unit.diagnostic is not None else {}))\n'
                                               '                               if isinstance(unit, '
                                               'V4FollowThroughFailureBatch)\n'
                                               '                               else '
                                               'self.writer.submit_strategy_one_entry_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4StrategyOneEntryBatch)\n'
                                               '                               else '
                                               'self.writer.submit_oms_tactic_v4(unit)\n'
                                               '                               if isinstance(unit, '
                                               'V4OmsTacticBatch)\n'))),
 'src/backend/replay_run_service.py': ('140ed6acfe829e689332b736fd26a4e6f67b13b34990fb5642c87e938bf419c6',
                                       '23abd12d12c280bff4a7388478ae7ba7710c7bd2cc3d793e6823c44ab41b23e2',
                                       (('            account_ids=account_ids, '
                                         'anchor_date=self.definition.session_date,\n'
                                         '            run_id=self.run_id)\n'
                                         '        from '
                                         '.backtest_fixed_structural_lot_configuration import '
                                         'declared_fixed_structural_lot_contract\n'
                                         '        selected_lot_session = None\n'
                                         '        if '
                                         'declared_fixed_structural_lot_contract(strategy_number) '
                                         'is not None:\n'
                                         '            from '
                                         '.backtest_fixed_structural_lot_execution_v19 import '
                                         'prepare_fixed_structural_lot_session\n'
                                         '            from .backtest_market_data import '
                                         'readonly_clickhouse_client\n'
                                         '            selected_lot_session = await '
                                         'asyncio.to_thread(prepare_fixed_structural_lot_session, '
                                         'plans=plans,\n'
                                         '                number=strategy_number, '
                                         'run_id=self.run_id, '
                                         'session_date=self.definition.session_date,\n'
                                         '                market=plans.market, '
                                         'candidates=plans.candidates, entry=plans.entry,\n'
                                         '                seeds=plans.seeds, '
                                         'through_boundary_ms=self._fixed_through_boundary_ms(),\n',
                                         '            account_ids=account_ids, '
                                         'anchor_date=self.definition.session_date,\n'
                                         '            run_id=self.run_id)\n'
                                         '        from '
                                         '.backtest_fixed_structural_lot_configuration import '
                                         'declared_fixed_structural_lot_contract\n'
                                         '        selected_lot_session = None\n'
                                         '        if '
                                         'declared_fixed_structural_lot_contract(strategy_number) '
                                         'is not None:\n'
                                         '            from '
                                         '.backtest_fixed_structural_lot_execution_v18 import '
                                         'prepare_fixed_structural_lot_session\n'
                                         '            from .backtest_market_data import '
                                         'readonly_clickhouse_client\n'
                                         '            selected_lot_session = await '
                                         'asyncio.to_thread(prepare_fixed_structural_lot_session, '
                                         'plans=plans,\n'
                                         '                number=strategy_number, '
                                         'run_id=self.run_id, '
                                         'session_date=self.definition.session_date,\n'
                                         '                market=plans.market, '
                                         'candidates=plans.candidates, entry=plans.entry,\n'
                                         '                seeds=plans.seeds, '
                                         'through_boundary_ms=self._fixed_through_boundary_ms(),\n'),
                                        ("            configuration['strategy']['strategy_id'], "
                                         'strategy_number)\n'
                                         '        from .backtest_declared_ladder_plan import '
                                         'automatic_policy\n'
                                         '        from '
                                         '.backtest_fixed_structural_lot_configuration import '
                                         'declared_fixed_structural_lot_contract\n'
                                         '        selected_lot_session = None\n'
                                         '        if '
                                         'declared_fixed_structural_lot_contract(strategy_number) '
                                         'is not None:\n'
                                         '            from '
                                         '.backtest_fixed_structural_lot_execution_v19 import '
                                         'prepare_fixed_structural_lot_session\n'
                                         '            from .backtest_market_data import '
                                         'readonly_clickhouse_client\n'
                                         '            selected_lot_session = await '
                                         'asyncio.to_thread(prepare_fixed_structural_lot_session, '
                                         'plans=plans,\n'
                                         '                '
                                         'number=strategy_number,run_id=run_id,session_date=definition.session_date,\n'
                                         '                '
                                         'market=plans.market,candidates=plans.candidates,entry=plans.entry,seeds=plans.seeds,\n'
                                         '                '
                                         'through_boundary_ms=controller._fixed_through_boundary_ms(),\n',
                                         "            configuration['strategy']['strategy_id'], "
                                         'strategy_number)\n'
                                         '        from .backtest_declared_ladder_plan import '
                                         'automatic_policy\n'
                                         '        from '
                                         '.backtest_fixed_structural_lot_configuration import '
                                         'declared_fixed_structural_lot_contract\n'
                                         '        selected_lot_session = None\n'
                                         '        if '
                                         'declared_fixed_structural_lot_contract(strategy_number) '
                                         'is not None:\n'
                                         '            from '
                                         '.backtest_fixed_structural_lot_execution_v18 import '
                                         'prepare_fixed_structural_lot_session\n'
                                         '            from .backtest_market_data import '
                                         'readonly_clickhouse_client\n'
                                         '            selected_lot_session = await '
                                         'asyncio.to_thread(prepare_fixed_structural_lot_session, '
                                         'plans=plans,\n'
                                         '                '
                                         'number=strategy_number,run_id=run_id,session_date=definition.session_date,\n'
                                         '                '
                                         'market=plans.market,candidates=plans.candidates,entry=plans.entry,seeds=plans.seeds,\n'
                                         '                '
                                         'through_boundary_ms=controller._fixed_through_boundary_ms(),\n'))),
 'src/trading_runtime/arte_journal_writer.py': ('98bdc5ffbd26cff46d58186f7f9019c262a990a4413dd117f4d3008035bfccd1',
                                                'c8f6dcb9d0bc40c03a51229dcd7f167556667e9eef7b0f0cbd57cda4ac3fb631',
                                                (('            return receipt\n'
                                                  '\n'
                                                  '    def submit_compound_v4(self, unit, *, '
                                                  'first_price_source=None) -> Future[str]:\n'
                                                  '        """Queue one bounded mixed V4 commit '
                                                  'without caller-side network I/O."""\n'
                                                  '        from .arte_journal_compound_v4 import '
                                                  'V4CompoundBatch\n'
                                                  '        from .selected_exit_publication_policy '
                                                  'import '
                                                  'requires_selected_followthrough_context_for_client\n'
                                                  '\n'
                                                  '        if (self._journal_profile not in '
                                                  'self._V4_PROFILES\n'
                                                  '                or type(unit) is not '
                                                  'V4CompoundBatch\n'
                                                  '                or unit.base.run_id != '
                                                  'self._run_id):\n'
                                                  '            raise ValueError("V4 compound '
                                                  'requires its pinned writer")\n'
                                                  "        if (unit.children['profit_givebacks'] "
                                                  "or unit.children['confirmed_ah_failures']\n"
                                                  '                or '
                                                  "unit.children['liquidity_fade_failures'] or "
                                                  "unit.children.get('original_risk_diagnostics')\n"
                                                  '                or '
                                                  'requires_selected_followthrough_context_for_client(self._client, '
                                                  'unit)):\n'
                                                  '            return '
                                                  'self._submit_profit_publication(unit, '
                                                  'first_price_source=first_price_source)\n'
                                                  '        if first_price_source is not None:\n'
                                                  "            raise ValueError('Compound price "
                                                  "context requires a profit witness')\n"
                                                  '        if self._journal_profile == "live_v4" '
                                                  'and any(\n'
                                                  '                getattr(unit.base, name) for '
                                                  'name in (\n',
                                                  '            return receipt\n'
                                                  '\n'
                                                  '    def submit_compound_v4(self, unit, *, '
                                                  'first_price_source=None) -> Future[str]:\n'
                                                  '        """Queue one bounded mixed V4 commit '
                                                  'without caller-side network I/O."""\n'
                                                  '        from .arte_journal_compound_v4 import '
                                                  'V4CompoundBatch\n'
                                                  '\n'
                                                  '        if (self._journal_profile not in '
                                                  'self._V4_PROFILES\n'
                                                  '                or type(unit) is not '
                                                  'V4CompoundBatch\n'
                                                  '                or unit.base.run_id != '
                                                  'self._run_id):\n'
                                                  '            raise ValueError("V4 compound '
                                                  'requires its pinned writer")\n'
                                                  "        if (unit.children['profit_givebacks'] "
                                                  "or unit.children['confirmed_ah_failures']\n"
                                                  '                or '
                                                  "unit.children['liquidity_fade_failures'] or "
                                                  "unit.children.get('original_risk_diagnostics')):\n"
                                                  '            return '
                                                  'self._submit_profit_publication(unit, '
                                                  'first_price_source=first_price_source)\n'
                                                  '        if first_price_source is not None:\n'
                                                  "            raise ValueError('Compound price "
                                                  "context requires a profit witness')\n"
                                                  '        if self._journal_profile == "live_v4" '
                                                  'and any(\n'
                                                  '                getattr(unit.base, name) for '
                                                  'name in (\n'),
                                                 ('    def submit_followthrough_exit_v4(self, '
                                                  'unit: V4FollowThroughFailureBatch, *, '
                                                  'first_price_source=None) -> Future[str]:\n'
                                                  '        """Queue the immutable exit and scalar '
                                                  'witness without network I/O."""\n'
                                                  '        if self._journal_profile not in '
                                                  'self._V4_PROFILES or not isinstance(\n'
                                                  '                unit, '
                                                  'V4FollowThroughFailureBatch):\n'
                                                  '            raise ValueError("Follow-through '
                                                  'exit requires the V4 writer profile")\n'
                                                  '        from .selected_exit_publication_policy '
                                                  'import '
                                                  'requires_selected_followthrough_context_for_client\n'
                                                  '        if unit.diagnostic is not None or '
                                                  'requires_selected_followthrough_context_for_client(self._client, '
                                                  'unit):\n'
                                                  '            return '
                                                  'self._submit_profit_publication(unit,first_price_source=first_price_source)\n'
                                                  '        if first_price_source is not None:\n'
                                                  "            raise ValueError('Legacy "
                                                  'followthrough queue cannot carry selected '
                                                  "diagnostic context')\n"
                                                  '        with self._submission_lock:\n'
                                                  '            if self._closed or self._error is '
                                                  'not None:\n',
                                                  '    def submit_followthrough_exit_v4(self, '
                                                  'unit: V4FollowThroughFailureBatch, *, '
                                                  'first_price_source=None) -> Future[str]:\n'
                                                  '        """Queue the immutable exit and scalar '
                                                  'witness without network I/O."""\n'
                                                  '        if self._journal_profile not in '
                                                  'self._V4_PROFILES or not isinstance(\n'
                                                  '                unit, '
                                                  'V4FollowThroughFailureBatch):\n'
                                                  '            raise ValueError("Follow-through '
                                                  'exit requires the V4 writer profile")\n'
                                                  '        if unit.diagnostic is not None:\n'
                                                  '            return '
                                                  'self._submit_profit_publication(unit,first_price_source=first_price_source)\n'
                                                  '        if first_price_source is not None:\n'
                                                  "            raise ValueError('Legacy "
                                                  'followthrough queue cannot carry selected '
                                                  "diagnostic context')\n"
                                                  '        with self._submission_lock:\n'
                                                  '            if self._closed or self._error is '
                                                  'not None:\n'),
                                                 ("            raise ValueError('Profit exit "
                                                  "requires its exact typed envelope')\n"
                                                  '        return '
                                                  'self._submit_profit_publication(unit, '
                                                  'first_price_source=first_price_source)\n'
                                                  '\n'
                                                  '    def _submit_profit_publication(self, unit, '
                                                  '*, first_price_source=None) -> Future[str]:\n'
                                                  '        from .arte_journal_compound_v4 import '
                                                  'V4CompoundBatch\n'
                                                  '        from .selected_exit_publication_policy '
                                                  'import '
                                                  'requires_selected_followthrough_context_for_client\n'
                                                  '        from zoneinfo import ZoneInfo\n'
                                                  '        if (self._journal_profile != '
                                                  "'backtest_v4'\n"
                                                  '                or type(unit) not in '
                                                  '(V4ProfitGivebackBatch, '
                                                  'V4ConfirmedAhFailureBatch, '
                                                  'V4LiquidityFadeFailureBatch, V4CompoundBatch, '
                                                  'V4FollowThroughFailureBatch)\n'
                                                  '                or unit.base.run_id != '
                                                  'self._run_id):\n'
                                                  "            raise ValueError('Profit "
                                                  'publication requires its exact Backtest '
                                                  "writer')\n"
                                                  '        selected_context = '
                                                  'requires_selected_followthrough_context_for_client(self._client, '
                                                  'unit) if type(unit) in '
                                                  '(V4FollowThroughFailureBatch, V4CompoundBatch) '
                                                  'else False\n'
                                                  '        if type(unit) is '
                                                  'V4FollowThroughFailureBatch and unit.diagnostic '
                                                  'is None and not selected_context:\n'
                                                  "            raise ValueError('Source-fenced "
                                                  'followthrough lane requires selected '
                                                  "diagnostic')\n"
                                                  '        selected_failures=((unit.failure,) if '
                                                  'unit.diagnostic is not None else ()) if '
                                                  'type(unit) is V4FollowThroughFailureBatch else '
                                                  '(\n'
                                                  '                           tuple(row for row in '
                                                  "unit.children['followthrough_failures']\n"
                                                  '                                 if '
                                                  "any(str(d['parent_record_id'])==str(row['parent_record_id'])\n"
                                                  '                                        for d '
                                                  'in '
                                                  "unit.children.get('original_risk_diagnostics',())))\n"
                                                  '                           if type(unit) is '
                                                  'V4CompoundBatch else ())\n'
                                                  '        if selected_failures:\n'
                                                  '            from '
                                                  '.arte_original_risk_diagnostic_v4 import '
                                                  'diagnostic_policy\n'
                                                  '            for row in selected_failures:\n'
                                                  '                if '
                                                  "getattr(self._client,'confirmed_original_risk_policy',None)!=diagnostic_policy(row['strategy_number']):\n"
                                                  "                    raise ValueError('Selected "
                                                  'failure writer lacks its exact declared '
                                                  "diagnostic capability')\n"
                                                  '        if selected_context and type(unit) is '
                                                  'V4CompoundBatch:\n'
                                                  '            selected_failures = '
                                                  "tuple(unit.children['followthrough_failures'])\n"
                                                  '        # A compound may start with a '
                                                  'present-day run-creation event. Only the\n'
                                                  '        # linked historical exit clocks attest '
                                                  'the native market session.\n'
                                                  '        exits = '
                                                  "(unit.children['profit_givebacks'] + "
                                                  "unit.children['confirmed_ah_failures'] + "
                                                  "unit.children['liquidity_fade_failures'] + "
                                                  'selected_failures\n'
                                                  '                 if type(unit) is '
                                                  'V4CompoundBatch else\n'
                                                  '                 (unit.profit,) if type(unit) '
                                                  'is V4ProfitGivebackBatch else\n',
                                                  "            raise ValueError('Profit exit "
                                                  "requires its exact typed envelope')\n"
                                                  '        return '
                                                  'self._submit_profit_publication(unit, '
                                                  'first_price_source=first_price_source)\n'
                                                  '\n'
                                                  '    def _submit_profit_publication(self, unit, '
                                                  '*, first_price_source=None) -> Future[str]:\n'
                                                  '        from .arte_journal_compound_v4 import '
                                                  'V4CompoundBatch\n'
                                                  '        from zoneinfo import ZoneInfo\n'
                                                  '        if (self._journal_profile != '
                                                  "'backtest_v4'\n"
                                                  '                or type(unit) not in '
                                                  '(V4ProfitGivebackBatch, '
                                                  'V4ConfirmedAhFailureBatch, '
                                                  'V4LiquidityFadeFailureBatch, V4CompoundBatch, '
                                                  'V4FollowThroughFailureBatch)\n'
                                                  '                or unit.base.run_id != '
                                                  'self._run_id):\n'
                                                  "            raise ValueError('Profit "
                                                  'publication requires its exact Backtest '
                                                  "writer')\n"
                                                  '        if type(unit) is '
                                                  'V4FollowThroughFailureBatch and unit.diagnostic '
                                                  'is None:\n'
                                                  "            raise ValueError('Source-fenced "
                                                  'followthrough lane requires selected '
                                                  "diagnostic')\n"
                                                  '        selected_failures=((unit.failure,) if '
                                                  'type(unit) is V4FollowThroughFailureBatch else\n'
                                                  '                           tuple(row for row in '
                                                  "unit.children['followthrough_failures']\n"
                                                  '                                 if '
                                                  "any(str(d['parent_record_id'])==str(row['parent_record_id'])\n"
                                                  '                                        for d '
                                                  'in '
                                                  "unit.children.get('original_risk_diagnostics',())))\n"
                                                  '                           if type(unit) is '
                                                  'V4CompoundBatch else ())\n'
                                                  '        if selected_failures:\n'
                                                  '            from '
                                                  '.arte_original_risk_diagnostic_v4 import '
                                                  'diagnostic_policy\n'
                                                  '            for row in selected_failures:\n'
                                                  '                if '
                                                  "getattr(self._client,'confirmed_original_risk_policy',None)!=diagnostic_policy(row['strategy_number']):\n"
                                                  "                    raise ValueError('Selected "
                                                  'failure writer lacks its exact declared '
                                                  "diagnostic capability')\n"
                                                  '        # A compound may start with a '
                                                  'present-day run-creation event. Only the\n'
                                                  '        # linked historical exit clocks attest '
                                                  'the native market session.\n'
                                                  '        exits = '
                                                  "(unit.children['profit_givebacks'] + "
                                                  "unit.children['confirmed_ah_failures'] + "
                                                  "unit.children['liquidity_fade_failures'] + "
                                                  'selected_failures\n'
                                                  '                 if type(unit) is '
                                                  'V4CompoundBatch else\n'
                                                  '                 (unit.profit,) if type(unit) '
                                                  'is V4ProfitGivebackBatch else\n'))),
 'src/trading_runtime/fixed_structural_lot_reuse_contract.py': ('84c1b4c34ff48365879a0614808da1d50216276384eb4bb597cec2a98cb5d204',
                                                                'a9d4ca28066be0faabc6aa7e6a8b5c0b34e853f4130bcaba2b9a6049aeb6301d',
                                                                (('        wanted = '
                                                                  'FixedStructuralLotEmptyConfirmationStrategyContract\n'
                                                                  '    from '
                                                                  '.complete_market_window_policy '
                                                                  'import INPUT as WINDOW_INPUT, '
                                                                  'RULE as WINDOW_RULE\n'
                                                                  '    if WINDOW_INPUT in '
                                                                  'release.input_contracts or '
                                                                  'WINDOW_RULE in '
                                                                  'release.rule_set_contracts:\n'
                                                                  '        from '
                                                                  '.fixed_structural_lot_complete_market_contract '
                                                                  'import '
                                                                  'FixedStructuralLotCompleteMarketStrategyContract\n'
                                                                  '        wanted = '
                                                                  'FixedStructuralLotCompleteMarketStrategyContract\n'
                                                                  '    from '
                                                                  '.selected_exit_publication_policy '
                                                                  'import INPUT as EXIT_INPUT, '
                                                                  'RULE as EXIT_RULE\n'
                                                                  '    if EXIT_INPUT in '
                                                                  'release.input_contracts or '
                                                                  'EXIT_RULE in '
                                                                  'release.rule_set_contracts:\n'
                                                                  '        from '
                                                                  '.fixed_structural_lot_selected_exit_contract '
                                                                  'import '
                                                                  'FixedStructuralLotSelectedExitStrategyContract\n'
                                                                  '        wanted = '
                                                                  'FixedStructuralLotSelectedExitStrategyContract\n'
                                                                  '    if type(contract) is not '
                                                                  'wanted or contract.release != '
                                                                  'release:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot factory "
                                                                  'differs from exact declared '
                                                                  "release type')\n"
                                                                  '    contract.__post_init__()\n'
                                                                  '    return contract\n',
                                                                  '        wanted = '
                                                                  'FixedStructuralLotEmptyConfirmationStrategyContract\n'
                                                                  '    from '
                                                                  '.complete_market_window_policy '
                                                                  'import INPUT as WINDOW_INPUT, '
                                                                  'RULE as WINDOW_RULE\n'
                                                                  '    if WINDOW_INPUT in '
                                                                  'release.input_contracts or '
                                                                  'WINDOW_RULE in '
                                                                  'release.rule_set_contracts:\n'
                                                                  '        from '
                                                                  '.fixed_structural_lot_complete_market_contract '
                                                                  'import '
                                                                  'FixedStructuralLotCompleteMarketStrategyContract\n'
                                                                  '        wanted = '
                                                                  'FixedStructuralLotCompleteMarketStrategyContract\n'
                                                                  '    if type(contract) is not '
                                                                  'wanted or contract.release != '
                                                                  'release:\n'
                                                                  '        raise '
                                                                  "ValueError('Fixed-lot factory "
                                                                  'differs from exact declared '
                                                                  "release type')\n"
                                                                  '    contract.__post_init__()\n'
                                                                  '    return contract\n'),))}

def restore_reviewed_parent_source(source, relative):
    relative = relative if relative.startswith('src/') else 'src/' + relative
    recipe = REVIEWED_PARENT_DELTAS.get(relative)
    if recipe is None:
        return source
    current, baseline, edits = recipe
    if sha256(ast.unparse(ast.parse(source)).encode('utf-8')).hexdigest() != current:
        return source
    restored = source.replace('\r\n', '\n')
    for new, old in edits:
        if restored.count(new) != 1:
            raise ValueError('Reviewed successor compatibility delta differs: ' + relative)
        restored = restored.replace(new, old, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode('utf-8')).hexdigest() != baseline:
        raise ValueError('Complete parent baseline AST restoration failed: ' + relative)
    return restored
