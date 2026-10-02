"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '3021ab1f454ea0d74f36623c2f7aaaeafe917a7abb56116d39aa127f02400f5f'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'edf5c200c98b8e2f021d96952d2a79fe5f46e9085f7d580fe0d4f7c7fe605cba'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'c29449ca87d14d3b66c4f644c7ba286fdad6423101b4b3f6ccf935c6ae74cce4'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '4ef162fc1e65ad08213d6b51b82a5d71a77627d38d75a5f8f6b3d0cbe987ca2c'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'eb79647935683952adfb6735e2eb0004e5e418dab1cb1f149813ee29270aff4c'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'e7a69fad14bba450e08a84ce602c621401d76f9671afdb1231abe569acb65818'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'e3915158079fdace8c1138a19baefe41444933ad17b4d771bfe5972ba5c9da5c'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '576119861b90a0931e75347ad81f7465fc1fa4d2542744e817b36dbc9a4e7df0'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'dbb4ed6fccdf457a23c9339d231c4e22c2716170b9b60fe47891747a379916a3',
                                       '_require_numbered_session_window': 'bb1a41c2e709fe5005138a246561435c061f850523ca5d18258b193d9f71d065',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '98f316de4da04fde500044328624aa4f96237cda44ac7a1e76151879e848c25a'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'f592c7ef8a0a77634650d8b01b3c3882ee31a33419afb30d6a90df4b3c204b5e'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '190d249b80c5e630e359582f1a9852d7f3be55dd0ce3aec7e0443c40cf4d2218'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '75808647b349bc92b046939362adce43f35ed76cd0767a8463c00aa194f37651'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '5d13530df770e50eaca93e3e92450d690a12404e070d93140e5a608117802268'},
 'src/trading_runtime/runtime.py': {'__module__': '96c7b16f3d707bd92a7c47124e8336de9538907109a7b7bb3cd21af69e8a8b65'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'fb43bc59d173903a802ffcc9510fcde7e6630456f8ee4dba8e832a171e9a56f9'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '19a0a5c8d37c4e1247aa41afb3c9320da9dd78718dcd7d8befd4a431a3649133'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '7adc5dfcd35ba5f30796cd9d980ab98d3bbc0aee25b0e95cc9dd5fc46ea8ad1f'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '44eeea0148374e1b3077ebf87cc9d57620d02790e5db45ba5bde4307cc298cc2'},
 'src/trading_runtime/strategy_registry.py': {'__module__': 'afa46ca933c2119456fd0c7aaf9a1d4f1e050aa5e7653583dd09203f3bd401a5'},
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
