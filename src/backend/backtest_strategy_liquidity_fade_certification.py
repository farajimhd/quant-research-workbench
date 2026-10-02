"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '2011cbc215acc9608d87531a2e22f93b63a603a056c1bbff04b4dfcb333e7286',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '2fa80b51bc237898b6481205882b8d206c74e3d680a23071e4ada2c39094fb2c',
    'src/backend/backtest_strategy_one_configuration.py': 'a5697d4122393cb49ed38a68764b1bc0971d4b77e78d442855ad8abd2628ba58',
    'src/backend/backtest_strategy_one_coordinator.py': '7494305fae29254ca2d483876b22f65be581faabe791a71e9bdba0659a289071',
    'src/backend/backtest_v4_saved_review.py': '1bc64f7788d6ec0ad664aff25242683bd192fb30a2bae2ebfa3821c19b9fdc7f',
    'src/trading_runtime/numbered_fixed_strategy.py': 'c47f675530b92b67c9553919990f58ad7ea7abc171a2b5f4eff04f98f28d2051',
    'src/trading_runtime/strategy_registry.py': 'a767b6a68e1d38819e9b065b576a592b97e888eb66d9e7459029f145456097b3',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'd5394af6261531b81e3bcca05e325d0f4bbd55307d34cb0fb7aed2edd998f1b9',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '793f7a0a9784f35fa71b5c12c2ab9681e890c701d5fdb6d1839ff6e2bc742633',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'bf2d86abddc2ec7ddb26e08d577f123eabe80e613605bc4ffc27242ddbcbc212',
    'src/trading_runtime/arte_profit_giveback_v4.py': '64e57d21312e4957e0201048728229f980b9676d675b8325904659b0e6269095',
    'src/trading_runtime/strategy_profit_giveback_source.py': '3bc702ba3f6d87e46f87322cb5865350f90be2c59f9b129c7eaf3cdc7cbdf4d9',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '4a2f5ea411642e446f6b4cd71922676964fe773980b2f88c96efd66aee81585a',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '7334979286692791d71e19800838b0b8093c3e207a4b39346d2b43c2f9f24cf3',
    'src/trading_runtime/arte_followthrough_failure_v4.py': 'e77f1b7446c0730afaf03e2e332cce18a0c8f86e13ff71d8730e75eb8b318d8f',
    'src/trading_runtime/strategy_followthrough_exit.py': '11d0fc904c401b4c76546f620d3fc8092e1a1e38acbebd2f665a293eb3d93c60',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '0d74c5c83b0c1130a9162dcf7c44376d62fa24eb2d152411c82fb2bcc30fdb8c',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '48bc8d6e3532eca6aab5585304e57d00c83ae03e6a6622cde5ec11472ae899a8',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'e8fb97c459d92dad42c16a365c2003f9888ee22a7c14faf407386bf3ff379929',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'f0de98520463c616b03bfe72911b39c4ec21c146712cb7106e1f1b0cc5b9ffa9',
    'src/trading_runtime/arte_first_price_entry_v4.py': '84a644d8a0347363409388ca891d963f100b7235c130bdea3001f8d443fcc125',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '58ef03a63bb3bfc57d998cd55f68b7c0357201e74b11b5b04d670fa543813e25',
    'src/backend/backtest_strategy_one_execution.py': '4e71f044f4deb794115be1cc81f3bb3eb59c0f588a56959da5c09ee6193830ad',
    'src/backend/backtest_strategy_liquidity_fade.py': '2262ffe427107eafe3d6d2e74431516bb55f5ba401efc0deeac3584b389e5c63',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': 'af069bc7e705adb3d7ffbf70ff45925fd2ecd6a25f3d9f471f4e4dd40291b09b',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'd2bf2492b926949f69fb815886cce559a0ae274ea4937eadda5163ee4a52b120',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bad5cf8604b46cc678dadea78aed716a8b828612bc5b45a69c86bbb5244977eb',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '2bd26158ad11dcbb6546ce71c0f5d4348d218235421dcbda98a206c8bc958a1e',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'ec1faf4bde235da17a4873bbf6db4853be8751c2bce45d39f4637c3565bb7b7b',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '18581766f8f3024b16f895ae5b2815bd62c22c5e5d934be90401b18d8fd0b89b',
    'src/trading_runtime/strategy_one_management_snapshot.py': '67bb28045cacae27fe8faf5ffe460e983248cc539447e511bf688dd6bd642361',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '7479906980a4d2a8c7b6a92dc99a6c73917e771d88801ad39076adfce5adb1c4',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '50d9177595e1126f4882f76f85b2c72a299ef002fd829dc181b5254e4cd74cc4',
    'src/trading_runtime/arte_journal_commit_v4.py': 'd5efc33b654317b3f50ac36b01b34c518fcd5db4f03bd8a3990dce344d580337',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '2b5e8abcf5caab0f6d50d5bcc1ec9fd26f712f1a3cdf25ef373fc46c9c046174',
    'src/backend/backtest_journal_memory.py': '824a8f8803af1d06073f509644d25cb50052379f24a259adc7ac819920d9e006',
    'src/backend/backtest_typed_projection.py': 'bd282ce151c36235eb3a881ecb14c26a6b463d35ebd934d9420b9fa015bf8af9',
    'src/backend/backtest_typed_publisher.py': 'c668f99f94a5c52da8fced7cb10c8c33bd1f32e76f784b7853e956db891f3613',
    'src/trading_runtime/runtime.py': 'c6dd993c3fa5b32bb0cf0414d0df1f92c487aff7dfeb9d9c1374f24cc89cb2b5',
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
