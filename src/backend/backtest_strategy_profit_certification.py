"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '571e90ade71025ce0fb71cea99895c0e20145b12dbefc1b2398efbd7a2601cbc'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '04e99b3a6699dee25a79546ac3062cfbda4291a50a13c71cb7b3a841962b12b7'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'd75eed6ac5a3404930474e524bff5f905ee4dc42cae83c6d27444a50f56b45df'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'b3da0885371cca61702e03110614a7fc1701edfa779a577ea1ff35ef75881270'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'feffe123c7935ee493cdba181474d56d078a3f4f525b980a1a3151f538c96d04'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '898697d401e8391cc7b6a83f19044365d9e49535ec808239f8ef4549942b2959'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'ef1af5aac44be18d27c909fcd7bc91bd6bbab14fa2f9da18f47b61dcad95a432'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '3570e265f99450c7993d50097bc7fa354eff8021355f6fdf41d1c556c44ee664'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '2b5da3079b14f327c40c744f84324ca3f0c0d164722a3f1fa2ec2037e8f649a6',
                                       '_require_numbered_session_window': '7edbe93e46d29429877002cde90f4b66521decd8cf8ff6a4e7f64a44e8fb919b',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'dd2cd07dacfda6e45e689c8aead1048163ccb5c11b9cdb3bccca986f446410b7'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'abff555cd4be1eb812ebac563f6910772f00a18c98ab0d7c537c3e405b12b83e'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'd97b8a2c841db218bd6f3c752499d738e51682ccbb38a123ef77c8b4b5d23217'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'fa8b8963889c932750e2d0505a205e3253d8f884cca1d8d96fdb457d191e03ce'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '4572b8612247111a011c396f39ef14d879dca99b6988a81b0cc5a60f9103e861'},
 'src/trading_runtime/runtime.py': {'__module__': 'c159dbf330294abaae988a5e59557e9dd81563690becafcd5646c64f786eadba'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '47fa9cda2196e01c1e30840fd2944d409700fbd71c240caeff161c0f1f32f54c'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '0c76d4c4e8e9149375f97e2aaf42fcee03c53c3138a5a6708f9ab8308055a81d'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '55f5acdf1e51a77356434ab95b4b57caaeef37ec561368cbaef3de0a11078011'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '75f18316301b3f1e41b39c72923d6ee9df3a4841150968837698334d9d9823e9'},
 'src/trading_runtime/strategy_registry.py': {'__module__': 'd6b06838efb9d86db9e8ccf258c0d434913a28a583cae0dd7bfa9dfbd4bfe842'},
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
        from .backtest_historical_strategy_projection import historical_strategy_tree
        tree = historical_strategy_tree(ast.parse(source), relative)
        for name, digest in expected.items():
            nodes = [tree] if name == '__module__' else [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
            if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                raise ValueError('Strategy 31 reviewed profit-route authority changed: ' + relative + ':' + name)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
