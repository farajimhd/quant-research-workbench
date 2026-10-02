"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '939e753aae2e760eeb717ba57e60a039a8a58642de3a41b6b36d16b8f3c0af42'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'f393b9d76a07a3f1e59dc428ef65b738c4a6e1cd83caee9735c7c8da3aedac67'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '87df1c3224fed7ea9ac5b4022f0937b07266bdfc902055c00e777ca951fa95d2'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'caee04929095369454fbcb9a21a08dab806a46216dd12d1f6beef8fa975fb77c'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'ae6bcd3071cff80292b89022760e228169b6b098d7f6c906f5b937b8228231f7'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '3a6399c1148f736b23d925a82055e801818cb61c6333f8bc2635db00c5697401'},
 'src/backend/backtest_typed_projection.py': {'__module__': '021a22a44902a013d37cf91b68a912f0cc1db41f60ed90dfcbc0470611c4f913'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '9ede7101106036958685b0dd00d38b95c8bbd52f42950470c5d6b5751dc6d8af'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '10b084751f7dd51f59f07138e3096166ac440b9f311984d613eabcd804cd7c68',
                                       '_require_numbered_session_window': '0bf680996d1bae59176f7c4041414067c7c94d42116ee8f8336b4bd005071c6a',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'b5037a969d67089413ae42f458aeb3fb430914b26df1fabd2595203275b33908'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '3eecfce9c507020cf9d9ba0eedaeefede50f563469830eb57dcf740b00b93a50'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'ce1bdbcd1fb0538f5c93cec107ac24ff4e1581c9dfb04910a31bb51cc52cfaa3'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '351dd4fcc8d977667ae2efa8682afffbc93229099ee1c6eb0bafd91ce302d2b2'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '6fbf9d79466681d33d1814582afd30870617ac70853b3bcc9eabe7fa31d6c36d'},
 'src/trading_runtime/runtime.py': {'__module__': 'b6d5daad1bbc5b14cc49dc0877eb6827db5a36ff5353d347b606a31c55b23581'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'a8ca8cc1a20e04266dd119171324fa69e73c9980b816976f89e1cc0a66dd2418'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '8de08c4d8dd32a018332ed9a3b332dd8773795c7d2a402b5646c942052af6c9e'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '628c00a7b89a38c1319a8c109c0be0bf405e9e042633e862dc241e77d582e711'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '692d0eb122537b90f036836878c5577013791a236326c7e89046b9e658f8a1b8'},
 'src/trading_runtime/strategy_registry.py': {'__module__': 'dd185fbef65cbfd90a530e743c358e82decb6441c0a905f8bd8ff12078090d70'},
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
