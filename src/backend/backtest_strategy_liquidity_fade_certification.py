"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '80081bad3f76f67437dcb8a507cb28551bbddac332ee9cd40bc53934273e48b5',
    'src/backend/backtest_strategy_one_configuration.py': 'a3157672b364f52abfbfe66d8ebdd0bda3e9f87506ef396349562644f83f37bf',
    'src/backend/backtest_strategy_one_coordinator.py': '6b4b10391952a7b4082bd35a7a10b5b909c78015e661a7b5d30a865a44cebe83',
    'src/backend/backtest_v4_saved_review.py': '011f90fd275e857302d18a3b763aceecbadb13658c081c6f36f45248ee33b707',
    'src/trading_runtime/numbered_fixed_strategy.py': 'fd1f4afae0ea2d0b3604c4bffb76a7d28e0132f470de587db4380c2ee8c0898f',
    'src/trading_runtime/strategy_registry.py': 'e04450c9bbbc4a6b3b312a820735c38b7c2dc9fe323aa6d087853c833d3b2ca9',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '08962ff6e0b738c5c2d6c3c0bfcfeb14d8f6982e5a2bd24dde1914e27a332667',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '06595951f580ba7fb3fd95d1c21ca6b9e1b3749729b1749cc1a2c72203a3718b',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '3829d15404a136fcd6c8c883bfd6bf7048323e3f7d33f7abb61b8d47ea080dad',
    'src/trading_runtime/arte_profit_giveback_v4.py': '3410ccea46575c540e58036cf537f72a26cc47e50ba5f75664722b62748fe7de',
    'src/trading_runtime/strategy_profit_giveback_source.py': 'e8b5daf8cafdfbede62b3ed042956554494e1dac840a48fb30d3995d83126f42',
    'src/trading_runtime/strategy_profit_giveback_arm.py': 'eb21ab1d2a25c5d411f4c6c1e109fb9f1e5290e1727585f49dbb5279d213059e',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'e459143bcdc9d1e2b58af0dd842df3dae6d21dada0e8b3d11a108a50f5fdaa6a',
    'src/trading_runtime/arte_followthrough_failure_v4.py': 'ee0d8cc1ec62681f96788c814941e080f1d6422ac409c31d96ff780ca2c1c3aa',
    'src/trading_runtime/strategy_followthrough_exit.py': '3a59eea878eb53399168f4f80e30abffbff67239cd5cef30f6337ca679c83975',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '8cca19a37ae307716282bfdf79e8d9efd610d4d54d66ee5dedfa744f36a5d4de',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '38d37a88754b664ca4edd13f1dfb1ed1b27b0b8fba74f29d0055e5e13504270d',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'e1d0497365cbac3d7a469e830222d018863f472ecf8f4564af1ba04bdbf83aeb',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'a5d5bf98eab9d2ef186bf6c816d847264d1b595a6413a02b96439bee6fade130',
    'src/trading_runtime/arte_first_price_entry_v4.py': 'd5cdb37515b24c127113ad38256c9379e2abbe2a2fc70fd321ef4d777f3f76a1',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '222f1109057dbfbf6a708431bcbeb631de9cc5f06be1ee2301a9fc81a0e5216b',
    'src/backend/backtest_strategy_one_execution.py': '1aeade532dfc89b8f319b64082609e586ff44d4a636ff8f04ce706289b469f9a',
    'src/backend/backtest_strategy_liquidity_fade.py': '39b5849330dfbb618216d9f0944b0dd2549444b2ab0ee2f5d33f3eed131621ed',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '2ae17b94d208e79b07343b159a70f231f940faaf9b9aaf23be8500b3e243aa14',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'ee0d4c9be912fc4b2d01887530f3d4f84e311c06351ef30f09645f43a88309da',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bf9c1c287bb9d1d043baacb3bedb0f2f82d74c1bd7b35fb8549fa1189cc17882',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'b04f6bf49bf27a4583a327cabca044808e42703efee0225c375271fdbf6f67fc',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': '17eb6f4e75f417d9ad5c52df1aa630195da499ed28565d04a97b6227a2707316',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'fa40b99b9c66ad6bee5b6d7920cb910562e0ff37cbe282861f47176b48ca29a2',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': '578ced5687416076679cd3326c74ca99e7a145ee10c420e8d9675e701e72e513',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'a7807c9ae461f72e9b00647ffb865f9a8f62e3c1f5c24657263084af2756b732',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '2afd93a156ce2925ae93c4705687851876b8b4a0c59e052a787caa9fd48081ad',
    'src/trading_runtime/strategy_one_management_snapshot.py': '09f7001316e46490ad19b85d0b0217cdf092f4cad4dd89c9d5902e3a49ad684c',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '4fc9fcec9ccb62c6f1b591b51cf7a15cfca278f8365c25783572d269ff593220',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '45fe16d3f2f1e9411230656fdbab0d3a3cc7bde956836b0752b912bd6b88bac3',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '2bb3b9c31207b9f2ea2a5ab7e8c6ed3c0b5fb07fcdf2a5e5599c051119017edf',
    'src/trading_runtime/arte_journal_commit_v4.py': '23253fa857cad44c0b617f11a4b82a156f68ef5854f394db05768e40181689e0',
    'src/trading_runtime/arte_journal_compound_v4.py': '4965c541673e52dffff094d170ef4a255f58fd2eeb154c3bfa2bf34c78b14c8b',
    'src/backend/backtest_strategy_certified_price_break.py': '0d2975f0451b6c54e43622794adb7b4b2d266f90414f491cdc6beb40b3f5abda',
    'src/backend/backtest_journal_memory.py': 'aebc9e470ca185086d847cb9b973318b88d5009844b79ed13c05be4a6e4b67d6',
    'src/backend/backtest_typed_projection.py': '7593347516c3365103d1f254cf0068178cf492f4d0184f7ff804b10c4c4e7886',
    'src/backend/backtest_typed_publisher.py': '0e346fc1352cd0c4e0fdaf76a7a4c110df5d4b0eec3ba3ec019496bdc8703a49',
    'src/trading_runtime/runtime.py': 'b8da3a1e6a65cd5419335cfc9af9e065a57ab76b7ba3f9bde31ab08681388729',
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
