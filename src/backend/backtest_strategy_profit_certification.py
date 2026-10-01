"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': 'f1fae823de13c213cb1540263b01955a7932f824e568d4038323c8656d703efb'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '2d86a263565b855d782de0960eef7e4be611e624c938233bb530ee3ae612e41f'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'b20575e1c216359d3ca7de5f7db9595bf1dc3997ba0024c9c1d2a688edf9411c'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '304ff3265b7948694f6de98c3544c4187732198e210a2f75b7c8cbb2e0000226'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'c714f7631415a6d01408b26b383c0885800ae06f77babd70195b25d41f6ee5e9'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '39f27504cd9b584807d1f12e316de4449628c9c46a5a48d2f315ff5bd47092fe'},
 'src/backend/backtest_typed_projection.py': {'__module__': '2ad52699afd1e79287e007284cb043ca89968399ad68c6b34b500c42491b1f01'},
 'src/backend/backtest_typed_publisher.py': {'__module__': 'ccd865df6b6daca6e285ca43b413541593a63bfd485000a7ab0363df88888d68'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '58801eac73e80c7381750fe5491f4d88a085d17e8eeabc2cd6a5c6697d1dc191',
                                       '_require_numbered_session_window': '25208973813c645047d01624a8ad0826e230bd55f54be44829fb4477f97e2051',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '4c38af37e1dc3d888d4d7c5f5e4f8a738125de8238c50913cc66f07549dddc2a'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '90bacc9a2c28335a1b33380f05e9d572a06ff0fce2fff2205647b363cc08b507'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'd2ff3c0521771d3574151f7ec3fce99affb0c37005a280768400767481112b05'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'c63300be126b3aa2cb236f4b78ee143bbba935942a36df69728db444330e5ce7'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '62b36840c30ed2558ec4bdb2d7a86a12db58e5e2d46ff448926f2f02e269f0a1'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'e7baf8a81a224e1a76bbc907f97383f5a120ddb28956592a631bd980dc6760f3'},
 'src/trading_runtime/runtime.py': {'__module__': '7ee44be131d03ffc8d9beca0931c18c86fd5b91f0fbe1e951864da249a2d648d'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'bb9957a3fcbc1ac1a137d0a4a76d8808ec0f004c6bb4f39f8d0b13bdadc1b74f'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'b16ef8c12e4b236040f6527b7f297738cfcc366cc61170cb8c903974f9df4352'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'd8954e89ee97428eb88413a6184dcb157dc5dc378429dfdf0de032a9a49c69b3'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'd89d3cbc6d571a55c91f6a9b30b98ff42cf6416963c7dc1fa9531292433c0eda'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '8ee586a1c3756e8d11eaa664c6f76fe0250b018ce7197241425149bafd110b66'},
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
