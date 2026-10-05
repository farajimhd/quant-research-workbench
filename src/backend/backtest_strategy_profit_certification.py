"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '717a5e4a4f5a31806282b3f7b37ba88c04e3862cd8413d49ff0f1bf79bc1277e'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'e3f87d0517f559cdf90de7407092d3aad20e0a622cb551291257dc28f791b481'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '4dfbb938081cdcdd651bd2a148f73e498338df141a20f162bc5507e0e3c77852'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '5eba2fbe247ced68f8d62ff653ceb68fa1486e8e0dbdf00140c88f32604b10c5'},
 'src/backend/backtest_typed_projection.py': {'__module__': '22ab5cb4116e3509b27c882a6234adb16e38e9853146014d9cb4b3eca4bdba68'},
 'src/backend/backtest_typed_publisher.py': {'__module__': 'a211e08727e013d4429582960c2e9c60a9d82348d4bb2248aaf2a189ea864be6'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '7295502f140c75ec817e2d47f6189ed036733868c8ebbabc7837c1a54d5d07ae',
                                       '_require_numbered_session_window': '83d935473001ef40bb4922a38c16fc1f2008b138fe272c10e074263d54e24391',
                                       '_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'c1f3fa36c9e782ba6badb204b85ffe58acdfe044e40f52228c22b2e6aef5c3eb'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '6c7b13c39de95592840b2224c202bb0b00e996076612bd6514e10b6c0d93204f'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '12d74f7a51be8ee01483ef8f0147b9d77fb878823c6fd6f3b4757c4167b99caa'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '9486fcfd61799446b81abd8e00550e0858bd6c197cb09ba209c0735261d7cd9e'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f'},
 'src/trading_runtime/runtime.py': {'__module__': 'c932991d4183d8dd42999157e8370d5c0e57ce411736e8de45ce1f64fc740a2e'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '76102fb098cbd89d47ec86849597aae7b0fc65f79263ace8455d30d79c43eb73'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '19c91475dc45da258bab57f6f27c2dd7fde8fdae3448f3245c0ac83ed28525d6'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '62f82e3e86c45eee06f382b17e0741c44374810688e2ff17207b72750abeddc4'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '2c278cd711240f22f24a491231bfc49a2366323a966f4e49f1c9d900a1a98ac2'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe'},
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
