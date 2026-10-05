"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '212d1bd2cca308ae71c4a4c711747faf5ea3cc34689e5a0f078984cd3339a54d'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '961c76570d96b47b086e591701bba35cf0b7e842a674537e3fddc880fce87b5d'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '5ea3a8b17b014e119e6ae181202d815fd8307346b5adf3d694f6538d43c1bd5e'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'bcb57fe0c67bb308c5d7b00f0ba73a4e10c8532dc1096ee61be217f06e662cc3'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '141e77c828cb552ade7a82fb78c665739b3ca699e8bf1449f8ec642c7b82442b'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'f1eaa31e0b38d3ab80759da1e78a23b1f239646ffe4d3fd7a4e3c2dacdd5bb08'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'b21dcf3f98eaf9c79b148741d0626f19e6af1eb2606c7d6ceb9694abd0613c86'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '1cf4b1011aa004219f4eb8d4398f9edea985eebb1092c4f13a299b65635b8e41'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'aa9451cce1ffccf481d2f113e975c76fcdffc146d086fd7e5ff004c771188f00',
                                       '_require_numbered_session_window': 'bcc67eb5aa8fce5fce411003a6f5cdb676971da477d5adec134abeb1c0a7ab5c',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '9ec544186b1c180821151e8e96930a3d654e6a7fc9818cb5e7e8847fabcefd8d'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '58a21b514d5443ad135d73869149a3e361ef04199627ffedfa04e64d1e3d83f2'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '4232070029a03c6b26c0ab266ddea4d447595348c0ed2918deb80753f8c91c56'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '6335a415be1cb3e4d15832f2bf4fbe705e0c5a844519445f627a1f762e191317'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'f9cdbb5afd51b1d9ccad16e42d89b7380c77f7ecad2c48cd785aed63ecaf98b0'},
 'src/trading_runtime/runtime.py': {'__module__': '6794e59df7b88582c32a8ba14009d1dbbcfb0cd172d2a51cb0efae6142457b58'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '3cf52e52b1030e5e4d197a14f84493cbd6534a7d7f99537b37a3f4c644f3086d'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '96843a8af2eba81358d598f88cbd089a5fd722ee49efec4eb346c511f9baf046'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '3b8850129e7f98f9c983004f0f7bd202d0f71da17ed4a4cc7fbb1564721216fb'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'df6937e04877828447292556fd3b81321691a5e30d2e9e3d9fb639c418c42b69'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '26edcb6ae79806ee700e50bc3ca771aa5004dcb8c58a6ab6ea3d116feecf4be1'},
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
