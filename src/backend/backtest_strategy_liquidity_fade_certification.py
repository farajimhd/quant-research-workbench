"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'a1c831ca875a550b557b70738727b962b7f6949a7e9863c975000ddd8b7dd8bc',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '939e753aae2e760eeb717ba57e60a039a8a58642de3a41b6b36d16b8f3c0af42',
    'src/backend/backtest_strategy_one_configuration.py': '87df1c3224fed7ea9ac5b4022f0937b07266bdfc902055c00e777ca951fa95d2',
    'src/backend/backtest_strategy_one_coordinator.py': 'caee04929095369454fbcb9a21a08dab806a46216dd12d1f6beef8fa975fb77c',
    'src/backend/backtest_v4_saved_review.py': '3735576db82a5861e540a6014d742df3db2ca638cfa56303e0e5e9df9ea887ca',
    'src/trading_runtime/numbered_fixed_strategy.py': '6fbf9d79466681d33d1814582afd30870617ac70853b3bcc9eabe7fa31d6c36d',
    'src/trading_runtime/strategy_registry.py': 'dd185fbef65cbfd90a530e743c358e82decb6441c0a905f8bd8ff12078090d70',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '331d3ddd69d8cd8b047f5812e4011c5d58ddc3b49544bd891fba150287c33b11',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '39071b704574e809a30c4becdadf1d050e5f096ab4197ac623212d41ee86a64b',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '0fab6edee92abbc1f254585e72d376ca5d8bbfbd1c8688f6360a4cf2ade52670',
    'src/trading_runtime/arte_profit_giveback_v4.py': '351dd4fcc8d977667ae2efa8682afffbc93229099ee1c6eb0bafd91ce302d2b2',
    'src/trading_runtime/strategy_profit_giveback_source.py': '692d0eb122537b90f036836878c5577013791a236326c7e89046b9e658f8a1b8',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '8de08c4d8dd32a018332ed9a3b332dd8773795c7d2a402b5646c942052af6c9e',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '628c00a7b89a38c1319a8c109c0be0bf405e9e042633e862dc241e77d582e711',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '1787bfa2f17569923334c25b605461a39be39537a0336a2924f6bb3879bb3a2b',
    'src/trading_runtime/strategy_followthrough_exit.py': '60d75706463e91591c974888ea9ede1f842f6b148d49f6d9ad78dacc5066b639',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '16b63cc31b47e7498ca18e1bc736bd88c7e315b7ea9428ac303bd0239c892d7b',
    'src/trading_runtime/strategy_rising_momentum_witness.py': 'fa363bfdcbdd546fdc859fdebbf09847f80e3eb0a521c6ded5483e3f95a25a13',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '91f8e9a4a293651aba8fe23b814d4a7789335b35c052e2fd5288b9db7f8b8872',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '108381b501a06b2d70ee263237d426e590579a94104c2559acac217d153c517e',
    'src/trading_runtime/arte_first_price_entry_v4.py': '9586bbbd02c0e1c46527784956cbb17c92d202c8f29107205622f5a52192f7b4',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '9c0f5276f220d163deb5e10c75a05343954727f9ccff2f073d278146e2cedf94',
    'src/backend/backtest_strategy_one_execution.py': 'ae6bcd3071cff80292b89022760e228169b6b098d7f6c906f5b937b8228231f7',
    'src/backend/backtest_strategy_liquidity_fade.py': '46ff877355462d2a46cebb81c8bf751000ef4a98d7bb45feff94d098e8c999e8',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': 'dda193b4da2db3db98c1026ae3c53eedf86b1895c02a1b978fdef6d16c2f9322',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'f7c53e5a13c630d48b858e676fbd8b25e4679ff4865ee242a2649ed8c6746b42',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '5693678981a8f9c42186d237aed4f2f96eed8dc94158128489817ca6017730e9',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '31a52a9f40c88fea85012d441db2f86deb0d6e9ef37003dcee15cfc0f8267d60',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': '3eecfce9c507020cf9d9ba0eedaeefede50f563469830eb57dcf740b00b93a50',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '3a6399c1148f736b23d925a82055e801818cb61c6333f8bc2635db00c5697401',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'a8ca8cc1a20e04266dd119171324fa69e73c9980b816976f89e1cc0a66dd2418',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': 'ce1bdbcd1fb0538f5c93cec107ac24ff4e1581c9dfb04910a31bb51cc52cfaa3',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '958216ce9c42afbf97d97e79937321d26a15473ac937afb08695be0cb1cf8e8d',
    'src/trading_runtime/arte_journal_commit_v4.py': 'b5037a969d67089413ae42f458aeb3fb430914b26df1fabd2595203275b33908',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '5e80003b263b24e07f4b6033e03ac769b95b5065daa86b671de982516234f03a',
    'src/backend/backtest_journal_memory.py': 'f393b9d76a07a3f1e59dc428ef65b738c4a6e1cd83caee9735c7c8da3aedac67',
    'src/backend/backtest_typed_projection.py': '021a22a44902a013d37cf91b68a912f0cc1db41f60ed90dfcbc0470611c4f913',
    'src/backend/backtest_typed_publisher.py': '9ede7101106036958685b0dd00d38b95c8bbd52f42950470c5d6b5751dc6d8af',
    'src/trading_runtime/runtime.py': 'b6d5daad1bbc5b14cc49dc0877eb6827db5a36ff5353d347b606a31c55b23581',
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
