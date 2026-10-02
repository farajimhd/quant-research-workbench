"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': 'dce2f6e9d03186d6ce38a4b61fc4d75b36ed6b25c70f60eefce632415b6daa86'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'b93041380117c358d0f7b14f2452450eeb5183909653721feaa6752b9e523abd'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '325be0505bb7b4ee6b8b9bd0d251134cbd3d0b9e42035c27684bf29569bcf31b'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'cbb70c053c61230aacdd5df2555804f6c9ae6bece533fe00f240855cab1e8a93'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'ec3ce757c835302831536b8678c251fe1ce158375a112c114c0320fcfa74968a'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'f9b95068a998419ca84515ac0b5c04f5d75ec4aca77da157ecc8d8d788e7ea00'},
 'src/backend/backtest_typed_projection.py': {'__module__': '1b76a74195d612057549a860c5212b3fbfab810151d37305d2e4a01b89e7cf6e'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '3215412966de8d38227427e09ff7059aad06b54cfc1f0d94e7c70c82dc7b3cee'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'fa52418e2bcfbbf3227e1460bb97c6bbffdccd7adddca9afc6978827a8059049',
                                       '_require_numbered_session_window': '7a316c6ffae3a461d9c193a3c257c6235f4ea5574661bff27f3d2243a55b5a6d',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'e64437ceee0702932aa2c39a6975e99deb13994887818366e2709da0722057e8'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'dcb21d67c419582d5c08d38cf8bca0edb63896b4dc262e2b63a730270d6e7b5b'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'deb8b1e562556a00d1d79b6cc8e8fa4b7cec4a1a5713e9acc088fdcc1be5f8b6'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'eb9bdd2ab1e237775cf2c661ffd23d297be8f6176ba8e364a81f8c74f8e39b7e'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'af28912cdc86a6d01e8591b511c9bf6a74088049bfd4bdda69e3cca0744dabc4'},
 'src/trading_runtime/runtime.py': {'__module__': '6873227c20b948211785c80507878de0e3dea5a03bea3f0361fba57efa856878'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'b61466191c397b7320cffa38d2e1127615e4968dd18a287608605a657f602172'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '50cdeaee216cc08ddf24c1f5b8fb50a2553528c189aa1990506cea8261d66315'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '8ebeed831c920f733251d466baccaee7bb69af8b6386950e28f10f6290b38082'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '6789c7f48ba035a8aa87e60991c82250a21e0464b1e66ad26b7fa57b076d6fba'},
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
