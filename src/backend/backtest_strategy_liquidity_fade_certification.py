"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
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
    'src/trading_runtime/arte_journal_writer.py': '02a5ec429f8a086447af9e5f077264b3ebee6c4f106626ff235b437903e40b97',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '16d5f12119c203dd89dd83d3d558724ea51fb20e76a0cf15dd54bb9b489c4925',
    'src/trading_runtime/strategy_one_management_snapshot.py': '31b474619299ae5734892c869b2885d19606b640a66cefad12a9d4e7566cfcd0',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '4fc9fcec9ccb62c6f1b591b51cf7a15cfca278f8365c25783572d269ff593220',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '72b320c9680f8eb03ad32181ea6a8594c847afb01a21a099eaef2e6bd245da07',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': '4499296baf688ccd93d81ac136a73cc416223a9a6291832736af7e776540e586',
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
