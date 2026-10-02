"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '6fff67894cd08c65969b9ebf26e58db942db8f6d80fb588b3e93c9544ace8a2f',
    'src/backend/backtest_strategy_one_configuration.py': 'f94b8fc15d933791948cc2e136ab835ca810fe93abcb483668d5639f5dd14b0c',
    'src/backend/backtest_strategy_one_coordinator.py': '7963a7e094838be998606b5a088b046cfaa8066e3ee0602cd9bb2bd9f890da01',
    'src/backend/backtest_v4_saved_review.py': '6b314337e553c6a1706feff61441375def1ba80517f7880a5b78aab7d1c2138c',
    'src/trading_runtime/numbered_fixed_strategy.py': 'a2f4d21fd878c2ac53088a45589633c942989e41ff085ef70fd83940797b9231',
    'src/trading_runtime/strategy_registry.py': '7292328bd2dbc0b7f84bf31758a88ad6203ecce5845c268305ea7150d434d314',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'e33a51296ff0e99e642de9385b2e802927d45ba40335afd0f77095c65a262615',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'd5b003faabf85559766ca780e07574a75dcff8c356f3895f4694ce9e837e070d',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'a7b0ef83820b8f22e2bf6beb42564731bc3141b2c9a8b88f29df20febba9770e',
    'src/trading_runtime/arte_profit_giveback_v4.py': '665ef3655d5a912de59e669159e4c64fc384c2f048f36b8a95b97f7bdf627575',
    'src/trading_runtime/strategy_profit_giveback_source.py': '20d27521c50a5f76dc2dd36ad204dea92c7b31359efd86a19e53cf62ab32247d',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '4a2f5ea411642e446f6b4cd71922676964fe773980b2f88c96efd66aee81585a',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '81954a09c6afa87c7f911f564407a8a5e142e5deebd53722a1f41dc1e2014dda',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '14463bae363d8495f5e703dd4b3396ba46f420bc0358b8b44cafe28e437f675d',
    'src/trading_runtime/strategy_followthrough_exit.py': 'ecda5d05feab72c8522c1f50c049536eb702024de94a4e6def5ad472e8e684dc',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '4c04dbe3b0a57ddebed4b07124bc4f31ae1b729385cd74c16c666ec8390d5b37',
    'src/trading_runtime/strategy_rising_momentum_witness.py': 'f902f6cc0a80bcd68db946140a955024d8451cc0e416d8d468068ac35ccf6dd5',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '8d40589ec224e8062383a0d73fc51b38e405de862fae86f98694d2de13f9a76e',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '6f958846fe5586dadc4fccf951399db4e1edfbd54d42b946ff10776ed38ccffd',
    'src/trading_runtime/arte_first_price_entry_v4.py': '0fd1535de2ada11051760f05ccae42309b2a896b00c995a2b011680a36d9617a',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '58ef03a63bb3bfc57d998cd55f68b7c0357201e74b11b5b04d670fa543813e25',
    'src/backend/backtest_strategy_one_execution.py': '682fc5080071a210aaca1b6f91fdd2cebf8c4fe11f05c4e3aa5c94f7ba2809cb',
    'src/backend/backtest_strategy_liquidity_fade.py': '39b5849330dfbb618216d9f0944b0dd2549444b2ab0ee2f5d33f3eed131621ed',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '69d73c2bc4d5f8576dafbcfb7b044bdcf8f0b16c499e1173f99052bcafbb571b',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'd42ea7ea24a9003e06e83cfe0313b97f25bd447ecfac8c64f43343817ae3887a',
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
    'src/trading_runtime/arte_journal_writer.py': 'cb91402b02e58f4c6ec2697f7cb764fc285b557128232aa0c6fc3de2eae6bea1',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '001e1108165718d1df344072ee7dbcaef6d08fbf3fdc2d2b6bcec9e6117dd0c6',
    'src/trading_runtime/strategy_one_management_snapshot.py': '25698c2a64d42a173a5e4e5f5a5433c3bedccb0f825730a16b04a6b3f515f844',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '16841678f27bfa15b475978a8bff89a0ba822c44aaafb7f59214c5498860f4af',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '68b47e544409bf5608c926f50dba5ca8c5e0584e13ae04b41543ed665af8bf51',
    'src/trading_runtime/arte_journal_commit_v4.py': '49c7ad6cb379143fb52b2d0cb0016696662884ed69a5cdee45a1cf4c27a3072b',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '3ff49d39f1667f5d8e84de70a9b30b286f785a987408deaa050941dff6857bd8',
    'src/backend/backtest_journal_memory.py': 'c118c12f2818fd0389d5503abb51e46fb45a9b05b8f2944dad0fc165d8e871e1',
    'src/backend/backtest_typed_projection.py': 'e4d256026cc694c4321457d27cbf200d208ac5f40b91049cf81964c27cf2f0e0',
    'src/backend/backtest_typed_publisher.py': '10cc6505a7704ce403e8b8fb4ff6db8637dc09d8f6bdd58d93171854d2ed5e48',
    'src/trading_runtime/runtime.py': 'ffb717541376b3d8e6809a2d8c0e24fe3f2a04e6bb2623dd5616372dd14e598a',
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
