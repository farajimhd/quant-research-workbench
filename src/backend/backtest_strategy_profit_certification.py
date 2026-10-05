"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '3d5ad0a8e627ca8f2ebdb64d469e5babbb49fd8fb349a1997a861b27185ddb6d'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '38697454c74a76fde23724929cf702f36ca9d55e013e0074b5a832891d4878f4'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'e704996f009d645191178125570420d5ff694577eedac9ff731eea0e4f1d8516'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'f0ee0c32e7b748bd79c912acc20d12ffa6ba8abda0749f95724d3610e6bd4281'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'a4b08fd1c0ed2bb667bddd221b191986156ca9b230d4ada2578d114cfbd84246'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '54815ec98ebcc11913e48e2f215261100a14860990908ee2c9fc2467aede867c'},
 'src/backend/backtest_typed_projection.py': {'__module__': '9270813823b23a636a362a82938882eb9672a730d263be50b73de44af25d4336'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '254e2c609b79f8e6bd2a6d109b4887a4ca7028c7dc89bfd3380453d6b7c72b9d'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'c224d99832824308abe0faab363122d4cf9262495f66ac37a522607f86124936',
                                       '_require_numbered_session_window': '47b4d1028193e9d88ebe9a0daeddf75a051e8d91764a14ebaaf9f1a69a630ac4',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '7916f90293d5493f0ac9dac3532ee616ec82e27f15d81f4d0f086215cc8b890f'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '9f57286328c1ece2f5a58e0f316f2d761dd616f4bad586d4932604f01d158c1e'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'a2eb28260f581a1e482570ad2f6658c678281c13efe76f84474196399c6749c8'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'd0959c19a0a239b16c1069b12dd77677fbf660111cb6ad32a77538a10a860557'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'f9c42ddd6921012fcf3d761a0fd718a8d207df5601d4440b682b40b0769a0784'},
 'src/trading_runtime/runtime.py': {'__module__': 'f9d753e2590b66b9bdfa55a7d6d083dc617dd790d8732e1030a5e43d4ad72acb'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '6f3644877c90947bf85f443285cfbe4588fe3964f89caa665ecda36af97fac85'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '7e36464b288c4b12f620aa18136314a68a9089999f884f8554b020dced2ca75d'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'fb5afe770f45580c9edcd3b1d4de8314d370418d910b9ebb0eef86fb79090dfa'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '07e5c09aa3e556487311fee18beafa327da2ef38f3f0c70d7810bc701985643b'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '65220011cea8e502d82fee5bbaf58d856e648ceace15dc48e2d0e9020e81f40d'},
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
