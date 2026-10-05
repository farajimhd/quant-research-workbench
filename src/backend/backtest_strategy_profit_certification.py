"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '5c26f11e3d64469f119a2d43c90c0c4295f51f09f9de653423f295de105b4b4d'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '45a2194f5bfc8930209ee105d7f833f0d58742be292fed74ba622814bdfa2b41'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '1e202198a9d406d398cec6abc3c57019410b6a498a1783aa0c8f52cb1ef1d547'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'd73db9cf82446d850817e02aade2ba84fa3925b7364dd6d550753b40f0579448'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'a9392473c840f7b3b1b4f4d385b86474c20a3272491610db5b6e610e3bb9cb90'},
 'src/backend/backtest_typed_publisher.py': {'__module__': 'b93dbcc0ab68998c19674c5b27c500d5295087015c8235afa3ff85dfe249b8fa'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '84e17527f8c05bd47cb17edda28fbb6210bc8366657e6561c425470b2f083820',
                                       '_require_numbered_session_window': '36e3d8dfe68e75e417926c7dd4569f2233c16888af7ee17f0340956c6886dc95',
                                       '_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'f07ed2f11699d904c1074704eb231257ae10acbfcb3aea1e83a16f10cd57f687'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'f243eb25533b27c2b3d7a15032767bf6cf966252746ea9ee79dcd311a3168526'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '2b4935058811e0f1b70fe091691cf0c5a4e9dd95e56e2083cb0f8a81d77a268e'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'ced95fa4696f2a92542113081be3dfea8c9795b94ef23bdd6c8c2b79e61c0eac'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea'},
 'src/trading_runtime/runtime.py': {'__module__': 'e100caac05294d45df561be0d05e25d2da2a0208a4c1ce18d20dc4d216c890ee'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '9dc93010d611c428a0c76e33d677db5d6cd3c645aa49698617ad1f70ed2fd9e2'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'cc45018c63f6dda87784686089ab407d9b3e0822751077cb4b296da5a9b4a45d'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'c871de29c87c01ce3901116bff3275565f6ebc35031145903fbc0e15e0d96b25'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '23352e4779dfeda5a49218e6c9aa9edce97bb5288d68367deab0d8b0fba45ef9'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487'},
 'src/trading_runtime/strategy_thirty_one_release.py': {'__module__': '2fcd0c7c34073032bf96e2007a901bd8fa99d120d6290a904e13a4e2c94f290e'},
 'src/trading_runtime/strategy_thirty_two_release.py': {'__module__': '66a79fcdd60af4cbf2b7d217340d7b8efa95576ffe694ae05aa597cdd61361a9'},
 'pipelines/strategy_one/strategy_thirty_two_configuration.py': {'__module__': '13a485fb66cdbfab61270b391441d2bb9613c65be77dcfed68eac0494428db7c'},
 'src/trading_runtime/order_management.py': {'cancel_numbered_session_acquisitions': '092a899af5f8fe3cf08b8cb139613350553b6322597be09503f489a0429bb92d'},
 'src/trading_runtime/strategy_thirty_three_release.py': {'__module__': 'cc0afd9027402150723a87f1e66526c7acf35aa78f113d1d5ec57b5675cd386e'},
 'pipelines/strategy_one/strategy_thirty_three_configuration.py': {'__module__': '20dbac5cf28f3f61409c187a9dfcc34b43da02eb81a161ce45ad70f53a4d060c'}}

def certify_profit_giveback_route_source(*, source_overrides=None):
    """Fail closed if any reviewed arming, order or persistence route changes."""
    overrides = source_overrides or {}
    if set(overrides) - set(REVIEWED_PROFIT_ROUTE):
        raise ValueError('Profit route override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in REVIEWED_PROFIT_ROUTE.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        tree = ast.parse(source)
        for name, digest in expected.items():
            nodes = [tree] if name == '__module__' else [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
            if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                raise ValueError('Strategy 31 reviewed profit-route authority changed: ' + relative + ':' + name)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
