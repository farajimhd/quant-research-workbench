"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'da93c31c96151883cd04196e956ab28319ac0943c2353b5b717719380d0606a2'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '79063bebc5d92af4e1704c6944fbbe2b81a2ba3780d8dc8de37dc21688bacfe1'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '6891230fd947b41e708aa458ce6563202135f867dc12a572e0a9f5f40f01b83c'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '43579b3257669e1f2dbee179bccd8cc0618b0c2eec0c0ce914104a5609d417f6'},
 'src/backend/backtest_typed_projection.py': {'__module__': '1fb9b10d98019e8a2291a8ef5a759fc4aee58995da63dc64c5657878f4a0d759'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '7134af423e8b564586532df8993c731761541c5d31883dd35c88d2525f6fcf04'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '5e11014ac50a0d7a506ab9ff568597dd73f8272abc9752a29dd9546e774f2f58',
                                       '_require_numbered_session_window': 'f8d6bc11ea461713c0556acd607b07af13265f11097601de53dd87b98560ea29',
                                       '_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '658edbb634d13c32bdf976d7098ba5f503f3d691579c820c29a64b74e66752c8'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'c9fd82b8dd967ee08771491b4dad695a82db9dc010326227a3a6e528f837c1c4'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '9037a91b71561a7b521d56c6428391c88add5505064d65bd6998a06f26e29c12'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '1707f58e93a71120e813ace622581b4eab5b2bff79c9a374b4fe5e4460e18299'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea'},
 'src/trading_runtime/runtime.py': {'__module__': '09e06f9cfa3bc9d6fbe3e590b04fe2787947a3e6be835839e3d1b1d10c38831f'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '4a114cc9d2a7f82000b518b1e89ec835cc0c9b270ed67f45ec315a462ba4555a'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '550d2c398f65237b70d353f2a55cc2ed37b9aaa74f3bf08aedc76397f0ffd18b'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '860e2b154c698d088432d85ba7db111fa381447c1ecbb25c37d895005032e42d'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '3895904b5ab3e86fa55923ce60fcc2a1dbd5cfce00d34d09e27cc71039740831'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8'},
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
