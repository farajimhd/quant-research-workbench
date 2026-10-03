"""Exact removal of independent Strategy 44 dispatch for older certificates.

Only reviewed prefixes are removed. Strategy 43 and historical policies retain
all their original bodies, constants and source-hash checks.
"""
import ast
from copy import deepcopy

REVIEWED_PREFIXES = ('if isinstance(getattr(state, \'group\', None), dict) and state.group.get(\'strategy_id\') == \'squeeze-grid-strategy\' and (type(state.group.get(\'strategy_revision\')) is int) and (state.group[\'strategy_revision\'] == 44):\n    from .strategy_forty_four_lineage import reconstruct_leg_orders\n    if any((row is not None for row in (followthrough_row, profit_giveback_row, confirmed_ah_row, liquidity_fade_row))):\n        raise ValueError("Strategy 44 cannot inherit another strategy\'s exit witness")\n    return reconstruct_leg_orders(state, source_intent, protection_history, admission_reservation=admission_reservation, admission_decision=admission_decision)', "if (expected_config.get('strategy_id'), expected_config.get('strategy_revision')) == ('squeeze-grid-strategy', 44):\n    from .backtest_strategy_forty_four_journal import StrategyFortyFourJournal\n    from .backtest_strategy_forty_four_publisher import StrategyFortyFourPublisher\n    if initial_sequence or batch_size > 512:\n        raise ValueError('Strategy 44 requires a new bounded 512-event writer lane')\n    journal_type, publisher_type = (StrategyFortyFourJournal, StrategyFortyFourPublisher)", 'if type(strategy_number) is int and strategy_number == 44:\n    from .backtest_strategy_forty_four_certification import certify_projection\n    return certify_projection()', 'if type(strategy_number) is int and strategy_number == 44:\n    from .backtest_strategy_forty_four_configuration import certify_configuration\n    return certify_configuration(client)', 'if type(number) is int and number == 44:\n    from src.trading_runtime.strategy_forty_four_release import verify_manifest\n    verify_manifest(strategy)\n    return True', "if (context['strategy_id'], int(context['strategy_revision'])) == ('squeeze-grid-strategy', 44):\n    from .backtest_strategy_forty_four_review import audit_terminal_source\n    audit_terminal_source(client, prefix, context)", 'if type(number) is int and number == 44:\n    from src.backend.backtest_strategy_forty_four_configuration import verify_envelope\n    payload, nodes = verify_envelope(dict(envelope))', "if strategy_id == 'squeeze-grid-strategy' and type(revision) is int and (revision == 44):\n    return True", "if (configuration.get('strategy', {}).get('strategy_id'), configuration.get('strategy', {}).get('revision')) == ('squeeze-grid-strategy', 44):\n    from src.backend.backtest_strategy_forty_four_preflight import preflight\n    return preflight(anchor_date=anchor_date, session_count=session_count, initial_cash=initial_cash, start_time=start_time, end_time=end_time, tickers=tickers, configuration_revision=approved, saved_review_authority=_saved_review_authority)", "if (strategy.get('strategy_id'), strategy.get('revision')) == ('squeeze-grid-strategy', 44):\n    from src.backend.backtest_strategy_forty_four_controller import StrategyFortyFourController\n    controller = StrategyFortyFourController(definition, runtime_root=self.runtime_root)", "if strategy.get('strategy_id') == 'squeeze-grid-strategy' and strategy.get('revision') == 44:\n    from src.backend.backtest_strategy_forty_four_configuration import validate_definition_sources\n    validate_definition_sources(self)")


def strip_strategy44_extensions(tree):
    signatures = {ast.dump(ast.parse(text).body[0], include_attributes=False)
                  for text in (*REVIEWED_PREFIXES, 'if executions and all((e.strategy_id, e.strategy_revision) == ("squeeze-grid-strategy", 44) for e in executions):\n        from .backtest_strategy_forty_four_performance import derive_saved_leg_positions\n        episodes, lifecycles = derive_saved_leg_positions(client, prefix, executions)\n        report = build_performance_report(episodes, executions, ())')}
    result = deepcopy(tree)
    class Remove(ast.NodeTransformer):
        def visit_If(self, node):
            prefix = ast.If(node.test, node.body, [])
            if ast.dump(prefix, include_attributes=False) in signatures:
                if node.orelse:
                    if len(node.orelse) != 1 or not isinstance(node.orelse[0], ast.If):
                        raise ValueError("Strategy 44 dispatch lost its original branch")
                    return self.visit(node.orelse[0])
                return None
            return self.generic_visit(node)
        def visit_Constant(self, node):
            if isinstance(node.value, str):
                node.value = node.value.replace("|42|43|44):", "|42|43):")
                extension = "OR (c.strategy_id='squeeze-grid-strategy' AND c.strategy_revision=43)\n               OR (c.strategy_id='squeeze-grid-strategy' AND c.strategy_revision=44))"
                node.value = node.value.replace(extension,
                    "OR (c.strategy_id='squeeze-grid-strategy' AND c.strategy_revision=43))")
            return node
        def visit_IfExp(self, node):
            if ast.unparse(node) == "512 if strategy_number in (43, 44) else 4096":
                return ast.parse("512 if strategy_number == 43 else 4096",mode="eval").body
            return self.generic_visit(node)
        def visit_ImportFrom(self, node):
            if node.module == "strategy_forty_four_lineage" and [a.asname for a in node.names] == ["forty_four_command_ids"]:
                return None
            return node
        def visit_Expr(self, node):
            if ast.unparse(node) == "strategy_one_commands.update(forty_four_command_ids(command_rows))":
                return None
            return self.generic_visit(node)
    return ast.fix_missing_locations(Remove().visit(result))
