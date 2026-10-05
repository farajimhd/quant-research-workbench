"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '6dc2a02199967979d5b93dc8b2af3ed3d9f2c7c6298dd12dd1c1decf5672f48c'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'b01f2373be42bfad44440e742b8c5476f12512afa1a7834a089f7175238799e9'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '5c2088264162096bbaa2ccef35253a1916cf20c676ed92052664fa253fabebc8'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '850e6ab6a644eceff98c66f988485845944d615bfbf1918fc54d6665797f61a9'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '81b15b9bb8f3feae00e4439f8edbbde89cfc3e9a445695bca70da1903010a05c'},
 'src/backend/backtest_typed_projection.py': {'__module__': '05081074d0168f9c6952df453f6c054079a220977a96eb70338e5543a7533d0a'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '316ccc51b7a9e93848439fb7eda43158282b2e0d6b59fa45e05c4779494e9233'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '76d973e9b518060a116f649a3656ecebb9e8d6405ee2a436c62d65d8ce397544',
                                       '_require_numbered_session_window': '55692e8c46c279acc4575206272b12d1cd0b6fae131ddf8610735fe1337d72a0',
                                       '_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'a94e1d8d06b377aa60106036ba42889e43c3631cea1698b562545576c361fbb8'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'e9a61cb75de23211d3059ea39e34a276e8736782f77a84f368c94d12208b6e0b'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '797fb4d6a314663d5c455eb8c1d0a712f6b94543b237a8d34da872b9076c37f6'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '2e49a2ce2cc531ffa0581c35988d0df2dfd975707554ef57406aa571fe2e08e2'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773'},
 'src/trading_runtime/runtime.py': {'__module__': '338a713370881103586864d0e83b51fcbaa9a55def4309b2213a62d9b095a3c8'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'a0e8acb006050ccde6ca19b6f9543e58d3fa0f080195e9b618bb4faacdd6df18'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'bd4b0b19dc4fce019ff2f82ae0f96a2b5e117484e933d2314a8a8be8f0740882'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '3bba33536111b224799b99a70aca545aed2f4ec7566279e092a65abe9aee9e22'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '985aa74ad51a20de1ccfcfa64bf5242e6df5a5aaec8b8404667dcfebdf6c6864'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60'},
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
