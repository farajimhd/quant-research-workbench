"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': 'acd6d0f2d1466576e782a7a0b62dc77488a145cc449a4251d175270f78e66780',
    'src/backend/backtest_strategy_one_execution.py': '102c195fb3d12550699d5767554b62be85bd57776529905152949d47124e80c7',
    'src/backend/backtest_strategy_liquidity_fade.py': '39b5849330dfbb618216d9f0944b0dd2549444b2ab0ee2f5d33f3eed131621ed',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '2ae17b94d208e79b07343b159a70f231f940faaf9b9aaf23be8500b3e243aa14',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'ee0d4c9be912fc4b2d01887530f3d4f84e311c06351ef30f09645f43a88309da',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bf9c1c287bb9d1d043baacb3bedb0f2f82d74c1bd7b35fb8549fa1189cc17882',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': '45949942e0dfe40d32562e83ef19ef61ecea567a050934b6da5a4f1c3ee50420',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '1d2045dcde4441a05b9c77a60126b7f0aec65010eaa45cc0739785d50b4ed7d0',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': '17eb6f4e75f417d9ad5c52df1aa630195da499ed28565d04a97b6227a2707316',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '5f05a969cf876ba2d254473a066325ae0d75e9516a74c7183ae3e18fe9bfd072',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': '578ced5687416076679cd3326c74ca99e7a145ee10c420e8d9675e701e72e513',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': '8664ce757cda248dcf5f3b53b3eb804878ae7de4ad26c3216bf68c8a5b973c9d',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '2afd93a156ce2925ae93c4705687851876b8b4a0c59e052a787caa9fd48081ad',
    'src/trading_runtime/strategy_one_management_snapshot.py': '31b474619299ae5734892c869b2885d19606b640a66cefad12a9d4e7566cfcd0',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '4fc9fcec9ccb62c6f1b591b51cf7a15cfca278f8365c25783572d269ff593220',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '72b320c9680f8eb03ad32181ea6a8594c847afb01a21a099eaef2e6bd245da07',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': '4499296baf688ccd93d81ac136a73cc416223a9a6291832736af7e776540e586',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '2bb3b9c31207b9f2ea2a5ab7e8c6ed3c0b5fb07fcdf2a5e5599c051119017edf',
    'src/trading_runtime/arte_journal_commit_v4.py': '13fd9224d0f01a0d7fb654c7a939a29528dbed4e604713e1f7a2e44487828aba',
    'src/trading_runtime/arte_journal_compound_v4.py': '4965c541673e52dffff094d170ef4a255f58fd2eeb154c3bfa2bf34c78b14c8b',
    'src/backend/backtest_strategy_certified_price_break.py': '25f4c07b74d1862a7579bf3ed7f69e0be6adb7c3734165fbef44a81498e84d23',
    'src/backend/backtest_journal_memory.py': '98af96c56278f5158cbf272a220fd4333d890217702c9b565e7173b4021ce60f',
    'src/backend/backtest_typed_projection.py': '905e4247b02e1592f281a656b7f96fef2cd782669f32f671b941c0e557918e41',
    'src/backend/backtest_typed_publisher.py': '61b48da127c109c782e8333af9c5ef746854966833f36f6d327b55dcb4119d32',
    'src/trading_runtime/runtime.py': '463f35bc6bfdf7748615f5108ff143779ebf505bb7b820f732a4ecddb1d07b16',
}


def certify_prepared_liquidity_fade_source(*, source_overrides=None):
    """Reject changed prepared rules, rolling arithmetic and authority checks.

    This is a prepared implementation seal. It does not certify inherited
    Strategy 34 routes, install Strategy 35, grant native writer admission,
    attest market inputs, or establish financial backtest performance.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(LIQUIDITY_FADE_SOURCE_AST):
        raise ValueError('Liquidity source override is outside prepared authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in LIQUIDITY_FADE_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Liquidity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Prepared liquidity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
