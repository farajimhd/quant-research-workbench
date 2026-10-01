"""Mutation checks for every exact reviewed profit-route source node."""
import ast
from pathlib import Path

import pytest

from src.backend.backtest_strategy_profit_certification import (
    REVIEWED_PROFIT_ROUTE, certify_profit_giveback_route_source,
)


def test_complete_route_covers_arming_orders_native_publication_and_recovery():
    for filename in ('strategy_profit_giveback_arm_reference.py', 'arte_journal_writer.py',
                     'arte_journal_commit_v4.py', 'arte_profit_giveback_reader_v4.py',
                     'arte_oms_projection.py', 'configuration_publisher.py',
                     'backtest_strategy_one_execution.py', 'strategy_thirty_two_release.py',
                     'strategy_thirty_two_configuration.py', 'strategy_thirty_three_release.py',
                     'strategy_thirty_three_configuration.py', 'order_management.py'):
        assert any(path.endswith('/' + filename) for path in REVIEWED_PROFIT_ROUTE)
    assert len(certify_profit_giveback_route_source()) == 64


@pytest.mark.parametrize('relative,node_name', [(path, node)
    for path, nodes in REVIEWED_PROFIT_ROUTE.items() for node in nodes])
def test_changed_reviewed_route_rejected(relative, node_name, tmp_path):
    root = Path(__file__).parents[1]
    source = (root / relative).read_text(encoding='utf-8')
    if node_name == '__module__':
        altered = source + '\n_unapproved_profit_route = True\n'
    else:
        node = next(n for n in ast.walk(ast.parse(source))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == node_name)
        lines = source.splitlines(keepends=True)
        first = node.body[0]
        lines.insert(first.lineno - 1, ' ' * first.col_offset + 'pass\n')
        altered = ''.join(lines)
    destination = tmp_path / Path(relative).name
    destination.write_text(altered, encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed profit-route authority changed'):
        certify_profit_giveback_route_source(source_overrides={relative: destination})


def test_unreviewed_source_override_rejected():
    with pytest.raises(ValueError, match='outside reviewed authority'):
        certify_profit_giveback_route_source(source_overrides={'foreign.py': Path('foreign.py')})


@pytest.mark.parametrize('lane', ['parent', 'profit'])
@pytest.mark.parametrize('number', [31, 32, 33, 34])
def test_full_numbered_certificate_propagates_either_route_rejection(monkeypatch, lane, number):
    from src.backend import backtest_fixed_v4_certification as fixed
    from src.backend import backtest_strategy_profit_certification as profit
    def rejected(*args, **kwargs):
        raise ValueError('rejected ' + lane + ' source')
    if lane == 'parent':
        monkeypatch.setattr(fixed, 'certify_strategy_one_v4_projection', rejected)
    else:
        monkeypatch.setattr(profit, 'certify_profit_giveback_route_source', rejected)
    with pytest.raises(ValueError, match='rejected ' + lane + ' source'):
        fixed.certify_numbered_fixed_v4_projection(number)
