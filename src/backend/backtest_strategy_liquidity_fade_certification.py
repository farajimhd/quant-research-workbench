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
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'e33a51296ff0e99e642de9385b2e802927d45ba40335afd0f77095c65a262615',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'dc29831ebafd42d2c8199f274acb2914f01525820d68803084923ad64406b880',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '51affe0adb53a22a2c5ee0ab1a4a517dc5e5656c9dcb88f79b46a5554f2eeb97',
    'src/trading_runtime/arte_profit_giveback_v4.py': 'eb9bdd2ab1e237775cf2c661ffd23d297be8f6176ba8e364a81f8c74f8e39b7e',
    'src/trading_runtime/strategy_profit_giveback_source.py': '6789c7f48ba035a8aa87e60991c82250a21e0464b1e66ad26b7fa57b076d6fba',
    'src/trading_runtime/strategy_profit_giveback_arm.py': 'eb21ab1d2a25c5d411f4c6c1e109fb9f1e5290e1727585f49dbb5279d213059e',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '8ebeed831c920f733251d466baccaee7bb69af8b6386950e28f10f6290b38082',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '24c82e9c40cede8f3a6fc58cd7ec33a2746fc26c08d0d22da8d63bdce7f36bb9',
    'src/trading_runtime/strategy_followthrough_exit.py': '369e348f8a0be9d057c09b61f28ee8724d4736469cd7c4624b8dd78c5ed43b76',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': 'e8e9d2f4f289dae1c04287c9a449a4779d8c7db6bab3263e077f03805817113a',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '567c85de2de7d58789d54b6a9194d504e0ed9cf8a23478d82bd4b066c8946671',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '32e9c6274783b916757f3b6a1ff8b991fac73a9c371912a2aaef379a0a3620c4',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '486a36b880d22e8bbcba709cca811063acbe81e990029551f201391b35a602fe',
    'src/trading_runtime/arte_first_price_entry_v4.py': '73aed2dd089e7ceb39a4f782f3579f7d32601f627acc4135245ff5c0797ab948',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '222f1109057dbfbf6a708431bcbeb631de9cc5f06be1ee2301a9fc81a0e5216b',
    'src/backend/backtest_strategy_one_execution.py': 'ec3ce757c835302831536b8678c251fe1ce158375a112c114c0320fcfa74968a',
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
    'src/trading_runtime/arte_journal_writer.py': '602b8aa8eb9f52e57c2f0856ef1812a7a633998e6b0287f426f309ca456d701d',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '2afd93a156ce2925ae93c4705687851876b8b4a0c59e052a787caa9fd48081ad',
    'src/trading_runtime/strategy_one_management_snapshot.py': '09f7001316e46490ad19b85d0b0217cdf092f4cad4dd89c9d5902e3a49ad684c',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '4cd080fb23c911f4a2a52f9b1b4bb116a96c76ab64243c502827a73d6261dfbc',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': '68b47e544409bf5608c926f50dba5ca8c5e0584e13ae04b41543ed665af8bf51',
    'src/trading_runtime/arte_journal_commit_v4.py': 'e64437ceee0702932aa2c39a6975e99deb13994887818366e2709da0722057e8',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '5e0f0b67941ee438748176becd38e04f6deeb89925b8cd3beff28a08f7cbc85d',
    'src/backend/backtest_journal_memory.py': '7e55871a7f133b9890696730e772dab0111b2c185e7dcbd10e4e824fc18dba06',
    'src/backend/backtest_typed_projection.py': '1b76a74195d612057549a860c5212b3fbfab810151d37305d2e4a01b89e7cf6e',
    'src/backend/backtest_typed_publisher.py': '3215412966de8d38227427e09ff7059aad06b54cfc1f0d94e7c70c82dc7b3cee',
    'src/trading_runtime/runtime.py': '331033696afed9862afa7f758fea08be10cd0654749a8ac6ff6879499e48c1cb',
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
