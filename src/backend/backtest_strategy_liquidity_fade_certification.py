"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': 'dce2f6e9d03186d6ce38a4b61fc4d75b36ed6b25c70f60eefce632415b6daa86',
    'src/backend/backtest_strategy_one_configuration.py': '325be0505bb7b4ee6b8b9bd0d251134cbd3d0b9e42035c27684bf29569bcf31b',
    'src/backend/backtest_strategy_one_coordinator.py': '7963a7e094838be998606b5a088b046cfaa8066e3ee0602cd9bb2bd9f890da01',
    'src/backend/backtest_v4_saved_review.py': '82f1ff7a6ead29eabe9839aaa19b065bf7963c97650eb805cfe582d0996084e2',
    'src/trading_runtime/numbered_fixed_strategy.py': 'af28912cdc86a6d01e8591b511c9bf6a74088049bfd4bdda69e3cca0744dabc4',
    'src/trading_runtime/strategy_registry.py': '679630a96826f88d684dbc0b87783dfbfa82f32aa35ab31af3f0535362f99677',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'e33a51296ff0e99e642de9385b2e802927d45ba40335afd0f77095c65a262615',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'dc29831ebafd42d2c8199f274acb2914f01525820d68803084923ad64406b880',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '51affe0adb53a22a2c5ee0ab1a4a517dc5e5656c9dcb88f79b46a5554f2eeb97',
    'src/trading_runtime/arte_profit_giveback_v4.py': 'eb9bdd2ab1e237775cf2c661ffd23d297be8f6176ba8e364a81f8c74f8e39b7e',
    'src/trading_runtime/strategy_profit_giveback_source.py': '6789c7f48ba035a8aa87e60991c82250a21e0464b1e66ad26b7fa57b076d6fba',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '50cdeaee216cc08ddf24c1f5b8fb50a2553528c189aa1990506cea8261d66315',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '8ebeed831c920f733251d466baccaee7bb69af8b6386950e28f10f6290b38082',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '24c82e9c40cede8f3a6fc58cd7ec33a2746fc26c08d0d22da8d63bdce7f36bb9',
    'src/trading_runtime/strategy_followthrough_exit.py': '369e348f8a0be9d057c09b61f28ee8724d4736469cd7c4624b8dd78c5ed43b76',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '4c04dbe3b0a57ddebed4b07124bc4f31ae1b729385cd74c16c666ec8390d5b37',
    'src/trading_runtime/strategy_rising_momentum_witness.py': 'f902f6cc0a80bcd68db946140a955024d8451cc0e416d8d468068ac35ccf6dd5',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '8d40589ec224e8062383a0d73fc51b38e405de862fae86f98694d2de13f9a76e',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '6f958846fe5586dadc4fccf951399db4e1edfbd54d42b946ff10776ed38ccffd',
    'src/trading_runtime/arte_first_price_entry_v4.py': '0fd1535de2ada11051760f05ccae42309b2a896b00c995a2b011680a36d9617a',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '0555a1ca78361ee63b6e4caf8a021f998aaa2f2f32ca876a380c37297ae43056',
    'src/backend/backtest_strategy_one_execution.py': '682fc5080071a210aaca1b6f91fdd2cebf8c4fe11f05c4e3aa5c94f7ba2809cb',
    'src/backend/backtest_strategy_liquidity_fade.py': '39b5849330dfbb618216d9f0944b0dd2549444b2ab0ee2f5d33f3eed131621ed',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '4118619e38ed617f383fe022d516dc772d1c85708fa71c5f53f07001ffedc53b',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'f27fea34c55eb9bf428a2e41bf9fe70a90506bd57ec3db38750f6485f298058f',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'b8f5330c85021d43f04930b6e28218b0b29fbce81cbee72a389663a8216b956c',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'b04f6bf49bf27a4583a327cabca044808e42703efee0225c375271fdbf6f67fc',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'fa40b99b9c66ad6bee5b6d7920cb910562e0ff37cbe282861f47176b48ca29a2',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'dcb21d67c419582d5c08d38cf8bca0edb63896b4dc262e2b63a730270d6e7b5b',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '001e1108165718d1df344072ee7dbcaef6d08fbf3fdc2d2b6bcec9e6117dd0c6',
    'src/trading_runtime/strategy_one_management_snapshot.py': '25698c2a64d42a173a5e4e5f5a5433c3bedccb0f825730a16b04a6b3f515f844',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': 'deb8b1e562556a00d1d79b6cc8e8fa4b7cec4a1a5713e9acc088fdcc1be5f8b6',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '68b47e544409bf5608c926f50dba5ca8c5e0584e13ae04b41543ed665af8bf51',
    'src/trading_runtime/arte_journal_commit_v4.py': 'e64437ceee0702932aa2c39a6975e99deb13994887818366e2709da0722057e8',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '3ff49d39f1667f5d8e84de70a9b30b286f785a987408deaa050941dff6857bd8',
    'src/backend/backtest_journal_memory.py': 'db2fccb5e65abb8cd68512636f0264711db18c331669feabdeac29765f3daeb0',
    'src/backend/backtest_typed_projection.py': '8e49df060d219ffa5fbf4f2eff4eddea86ca2fa5a9d1806405bc34f7b48e35e4',
    'src/backend/backtest_typed_publisher.py': '3215412966de8d38227427e09ff7059aad06b54cfc1f0d94e7c70c82dc7b3cee',
    'src/trading_runtime/runtime.py': 'e903eb4a5e6f08b4950c00c27ecd1f7e15c03a1fd459c40114cf30dd42b28561',
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
