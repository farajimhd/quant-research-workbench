"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': 'b6a7ce44ff0729b0eac8aa0dd0f2eac355661b9c1a76f1d94f159be28c5813d1'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '5bf8e6c016b00b7664f24c26ab35f5e977d71595548baa529fef3a0de42477f6'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '9fc30e92b34bb1690625dd63a3cd18bd0ddfb89497fd1df33c67bfec804fcb21'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '9549d626e2220efae49073992191cbf340630e28d971b5014cf5d7b10ec089cb'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '37eaef4da4a0f6503d5d98c33ab560df14b975f1ae571249171f55ac1631fab0'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'c4d3722667eb5024f09086f5e65ce258361028993a65eef2de6af69ff4f645a9'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'dad68574a6ca57515f41fa37898cf6023ea764a3bb93cd6ceb4b09a14503b20b'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '7030b248806e7d009f0a78bcb1abd47faec64444fd1779367d4993cbddd74bc7'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '1ce7bf81e6dc1d9e49ef29e977f5fafb6e49949cd22804e98cf41610423e957c',
                                       '_require_numbered_session_window': '3a87cb1e8a564028854be1df6504a48f3c0292a7261f34c77052619602a8ac8d',
                                       '_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '21eeb5914ff32bf6f2efa95fe1c3a3bb8deef581be6d9d350a951f70f20bcbc7'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '0057d79ac2d010ee112413c323404ffa8633e6b3879f90f892e6a92800c5c77a'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'b31004ffbfa21fb7a697e89031911a853175033597903ea9e2ca2891ccebe35a'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '233e9a504c592d1da4c447e96957ee3d59c114aeed3d6e8da48b9b9ff1e33aa4'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '59e01acb2138459dfa819c6853b8096688c15d9307ed03e2b1e841ebe01042ab'},
 'src/trading_runtime/runtime.py': {'__module__': '4dfba1e0baa9cebefea0d1625062debaa1b477ada24845a2adcdd10d885ffa30'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'cacbd10bd33f921f74cfe31926a8a51ab53f6f98c011a53444dd10b901a3104d'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'd30d3c40ec0da65f76eb964d9b78c4d55b9e78ecc31e9c0d70a8d88adb5773b9'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'c707b977dd847c82f8ed0acfa743970ce53a919ef60b7d06ec432deaa623b9b4'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'de1752c005b04c809b42217ba808792d27ba8704f9bd5aec01cc3f7eec26c2c0'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '56a9a7e178c7c062e7948ba495621e250572c15bd5063fff960e8c41a5ead0c8'},
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
