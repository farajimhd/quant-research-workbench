"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '01814aa5f9a1d174aca62bbf2c7333a34360bff984025408f39a9d1c3407e654'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'ae2bcf6e2a1d13930d7590947ae4d2729b4c5a370b2eaed2d5c7d532d9921bda'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '9d7b2ed8181d2a4a03350172a1b41bfb3937d44c8ed6e25be84dba9f13c5eca1'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '079fb3f1ecbd7751c70e47619c21d8ebf7fdb167eda91831656f504d9aed6839'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '38203ae8ef938df25ca4ee19bc1da65e39c272baeaf08bb0f04fdd27be3f7b4d'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '9ba7a1be877626617d0daa90b2c4de544f76bd45cb31ab297d1b585b0efb1432'},
 'src/backend/backtest_typed_projection.py': {'__module__': '317547c9ee0e818f818643ea15c09d5ecec40d418e8bd3702d710b41db9cfd96'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '56fb32933488d5b924ffbc82c8230138132329f79b4fe3afb7b2043aed655100'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '090dbf43500ec400015289c3819cbf110854028b2cdf85c5ed0e881f731ef055',
                                       '_require_numbered_session_window': '5ac0afe5593303e13de9b0bc533e5cb37a512bb0eabe3aed15f00f9154762be2',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '678e015fb946cab6cd84c1f053857f734abeb9aecce19130884043fa1534d70f'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '88485c336eed326ec192bfe6b0680de83daf047286a98d698d329532dc6faa82'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '9a13f46f0911158808e3e58844e0b004da288a99bd785ccfe2946353e5da60e7'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '72b320c9680f8eb03ad32181ea6a8594c847afb01a21a099eaef2e6bd245da07'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'f8b0b2ff9498e746f693016a474ff3c8ec33e2d3304105ddb95525c942a602cb'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'd39a2c2b5bed8e4ee3214e1fd3ccb0db0a694c8327c47c1afacd44b857665e06'},
 'src/trading_runtime/runtime.py': {'__module__': 'd3a7f4c28f4f2cd573235adefd9c6db7db6e006b54936751c17b39ad2418c18b'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '8792a8e578ee8d041602d20583705d56aa2c49b4b1d748553be73709b05540e7'},
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
