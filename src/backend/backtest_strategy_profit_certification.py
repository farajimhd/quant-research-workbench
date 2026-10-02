"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '01814aa5f9a1d174aca62bbf2c7333a34360bff984025408f39a9d1c3407e654'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '98af96c56278f5158cbf272a220fd4333d890217702c9b565e7173b4021ce60f'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '9d7b2ed8181d2a4a03350172a1b41bfb3937d44c8ed6e25be84dba9f13c5eca1'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '079fb3f1ecbd7751c70e47619c21d8ebf7fdb167eda91831656f504d9aed6839'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '38203ae8ef938df25ca4ee19bc1da65e39c272baeaf08bb0f04fdd27be3f7b4d'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '16d5f12119c203dd89dd83d3d558724ea51fb20e76a0cf15dd54bb9b489c4925'},
 'src/backend/backtest_typed_projection.py': {'__module__': '905e4247b02e1592f281a656b7f96fef2cd782669f32f671b941c0e557918e41'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '61b48da127c109c782e8333af9c5ef746854966833f36f6d327b55dcb4119d32'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '090dbf43500ec400015289c3819cbf110854028b2cdf85c5ed0e881f731ef055',
                                       '_require_numbered_session_window': '5ac0afe5593303e13de9b0bc533e5cb37a512bb0eabe3aed15f00f9154762be2',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '13fd9224d0f01a0d7fb654c7a939a29528dbed4e604713e1f7a2e44487828aba'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4965c541673e52dffff094d170ef4a255f58fd2eeb154c3bfa2bf34c78b14c8b'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '8664ce757cda248dcf5f3b53b3eb804878ae7de4ad26c3216bf68c8a5b973c9d'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '72b320c9680f8eb03ad32181ea6a8594c847afb01a21a099eaef2e6bd245da07'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'f8b0b2ff9498e746f693016a474ff3c8ec33e2d3304105ddb95525c942a602cb'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'd39a2c2b5bed8e4ee3214e1fd3ccb0db0a694c8327c47c1afacd44b857665e06'},
 'src/trading_runtime/runtime.py': {'__module__': '463f35bc6bfdf7748615f5108ff143779ebf505bb7b820f732a4ecddb1d07b16'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '31b474619299ae5734892c869b2885d19606b640a66cefad12a9d4e7566cfcd0'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'b4b1bc1e7ed0401969b718449e872823e95a0b0981d3294df4c567dce2e82029'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '8ae5d8d92cc51bb9f6862d3de66548765035d52fb9f3692957f8c018be385573'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '8619992bc728803ed2f738c96734c479e71667334c7fcf2c1e22757895935c97'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '18b0d2e813e865dba8cd95654a8fa7dd8f0de262a5149d52313d8182c9fda38c'},
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
