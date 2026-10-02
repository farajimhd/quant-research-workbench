"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': 'dce2f6e9d03186d6ce38a4b61fc4d75b36ed6b25c70f60eefce632415b6daa86'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'c118c12f2818fd0389d5503abb51e46fb45a9b05b8f2944dad0fc165d8e871e1'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '325be0505bb7b4ee6b8b9bd0d251134cbd3d0b9e42035c27684bf29569bcf31b'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '7963a7e094838be998606b5a088b046cfaa8066e3ee0602cd9bb2bd9f890da01'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '682fc5080071a210aaca1b6f91fdd2cebf8c4fe11f05c4e3aa5c94f7ba2809cb'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '001e1108165718d1df344072ee7dbcaef6d08fbf3fdc2d2b6bcec9e6117dd0c6'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'e4d256026cc694c4321457d27cbf200d208ac5f40b91049cf81964c27cf2f0e0'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '10cc6505a7704ce403e8b8fb4ff6db8637dc09d8f6bdd58d93171854d2ed5e48'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'fa52418e2bcfbbf3227e1460bb97c6bbffdccd7adddca9afc6978827a8059049',
                                       '_require_numbered_session_window': '7a316c6ffae3a461d9c193a3c257c6235f4ea5574661bff27f3d2243a55b5a6d',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '49c7ad6cb379143fb52b2d0cb0016696662884ed69a5cdee45a1cf4c27a3072b'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'cb91402b02e58f4c6ec2697f7cb764fc285b557128232aa0c6fc3de2eae6bea1'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '16841678f27bfa15b475978a8bff89a0ba822c44aaafb7f59214c5498860f4af'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '665ef3655d5a912de59e669159e4c64fc384c2f048f36b8a95b97f7bdf627575'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'af28912cdc86a6d01e8591b511c9bf6a74088049bfd4bdda69e3cca0744dabc4'},
 'src/trading_runtime/runtime.py': {'__module__': 'ffb717541376b3d8e6809a2d8c0e24fe3f2a04e6bb2623dd5616372dd14e598a'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '25698c2a64d42a173a5e4e5f5a5433c3bedccb0f825730a16b04a6b3f515f844'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '50cdeaee216cc08ddf24c1f5b8fb50a2553528c189aa1990506cea8261d66315'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '81954a09c6afa87c7f911f564407a8a5e142e5deebd53722a1f41dc1e2014dda'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '20d27521c50a5f76dc2dd36ad204dea92c7b31359efd86a19e53cf62ab32247d'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '679630a96826f88d684dbc0b87783dfbfa82f32aa35ab31af3f0535362f99677'},
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
