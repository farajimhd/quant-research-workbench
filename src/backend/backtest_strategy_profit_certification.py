"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '01cc6bd7baa7e1c30e352c71dd722d5e4511e59b6e1e8031b2be52bacce108e7'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'b078d07cbdda89aa9019f954d97cc865d77079dc0fbdf0d3dfccc3b63e5b4b59'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '9dfb89129a3c60d8c4eefa1d36f89ef6bc5c624c4c8106e474c5606e783ceaff'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': 'bc050062f8bb26626c700741a06557f3faef3cd2adc6ceab95abaec565894ea1'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '6e456027f060777e7451ddf67e4cf21514e0417fb6129648b004d169d4312830'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'a7367407db35e367c69bb5e073b11715eb0bf9d5ff7318830e86d20320da311d'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'c6eecad0d233d3bc3b7512fafb28d0d39982a9d1d4701725a598c457a51def9e'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '92f9182b6b1f382fd8dfffbb1f7a93f59723b770d6d49e059e945bc6191d5d28'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '5c54c3bc73e9291b652dd15b5c59259c9ab0ae14f019c5add381efa88040e427',
                                       '_require_numbered_session_window': '94f13ac9facb073b851ea8c47c0d484d0345512039748996fa30d5f3a97144a6',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'af6f05836e19bbba4b8cbf5cc11b3df4477be52be7365a1f994974e15afc7ba9'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '90bacc9a2c28335a1b33380f05e9d572a06ff0fce2fff2205647b363cc08b507'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'd14fd7b2429739e3a44f609b85ba068de44ad4ec1ff933c67de1e4c49afbad53'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '5635d0a8de53f235812c46ac8ae7f9f566a3b36805ab70cfe8b35556b347a992'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '98b846f81be755631ac9e89fa4a652f0c43ef7e30ef19c813aeb51ab88ca8c82'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '4075ee85ce0b0f92a8403abb89d41b5378691db45973ec8c701b99f4d5cc82e9'},
 'src/trading_runtime/runtime.py': {'__module__': 'e938f40ae3bf5f40f8b966b54b791485153a71bd0789e5a42399ef0f1449739f'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '37154371c277203ddf6576850df7f83eda0ebe5bfdd3f8658db4eaf1c599bcdc'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '2f1153deac085dc4ba9a302b939b0fb68d93f5cf00441ec139e28617d30ef42a'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'a1f47fd722dd0aae08c1e92016981d2e318ae8e629ca641962ed696d2b8138dd'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'be102d6ec3aea5ce7be170c3f7d9fb8fc8d7fefe38403fd5022d6b7400d7f810'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '009ecc3412bbad8f0186acfa7f93d2532788d6c22391e725a6f199955fca506d'},
 'src/trading_runtime/strategy_thirty_one_release.py': {'__module__': '2fcd0c7c34073032bf96e2007a901bd8fa99d120d6290a904e13a4e2c94f290e'},
 'src/trading_runtime/strategy_thirty_two_release.py': {'__module__': '66a79fcdd60af4cbf2b7d217340d7b8efa95576ffe694ae05aa597cdd61361a9'},
 'pipelines/strategy_one/strategy_thirty_two_configuration.py': {'__module__': '13a485fb66cdbfab61270b391441d2bb9613c65be77dcfed68eac0494428db7c'},
 'src/trading_runtime/order_management.py': {'cancel_numbered_session_acquisitions': '092a899af5f8fe3cf08b8cb139613350553b6322597be09503f489a0429bb92d'}}

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
