"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '00e678d1bb7960a43ca3fe3ac2d6e975273a84375a0ac82e8191c436439b1101',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'src/backend/backtest_strategy_one_coordinator.py': '79063bebc5d92af4e1704c6944fbbe2b81a2ba3780d8dc8de37dc21688bacfe1',
 'src/backend/backtest_v4_saved_review.py': '0645e6e3dacb127c3c50e8afc0bae4a867916822f3fd9310b60d8ad6a1c85928',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'a5fb97528e058d5d0fe62f68ed5f8301dc7d671be33ab18b7acadcca1a49ca12',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '6973202acb92d0c89aa93d62204fc26830b5f7d3acf7b9cc5c4e1a7198bd2b04',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'b3da5e8ac6d91f1ef26dfd5cc736a6325d89f8283a837a014ec411ef688a45fc',
 'src/trading_runtime/arte_profit_giveback_v4.py': '1707f58e93a71120e813ace622581b4eab5b2bff79c9a374b4fe5e4460e18299',
 'src/trading_runtime/strategy_profit_giveback_source.py': '3895904b5ab3e86fa55923ce60fcc2a1dbd5cfce00d34d09e27cc71039740831',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '550d2c398f65237b70d353f2a55cc2ed37b9aaa74f3bf08aedc76397f0ffd18b',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '860e2b154c698d088432d85ba7db111fa381447c1ecbb25c37d895005032e42d',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '30cd34d45f46c8e05ec7db910c6dc4eeed6691cd99c8f295534df0addfb8b235',
 'src/trading_runtime/strategy_followthrough_exit.py': '3e2dba19e2d894550c90f1de1638e7f857eb0ba97c22b6509aed938565ee47b9',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '6b962b8a200288cbf6094b2339033ae3c31c67c42c03dc1f9edb1d1bb43304fe',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '38a14626b96cb1076c7b519e8d720c75344797dc81abef4a744452a57db16338',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'a6d3715486ad6f89a66fd2d5e2211027943acc92b816d8f0cef8721122d128bd',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '17b8739a4a3d678eb2ef3f31e549d1a4b23b2b4260a40df519ec28b0223ad3cb',
 'src/trading_runtime/arte_first_price_entry_v4.py': 'a59674ba3faab920ab6666a90eb68d7a104d9e53d30303f664b7b2ab088db04d',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': '4291f16739779c5bd880a1049bf25f736bb32799b321770b6526e41dbe6c7035',
 'src/backend/backtest_strategy_one_execution.py': '6891230fd947b41e708aa458ce6563202135f867dc12a572e0a9f5f40f01b83c',
 'src/backend/backtest_strategy_liquidity_fade.py': '3769a7485d858aa74c0ec4b08a4cebe9a67ecacaac1f967e008e2be310abd174',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '257984b2c296ffb272d7f4d8bab2e3501a667702b2226060756d9f84e2981537',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '81045a270da89931b778971c2e974c219a2753f49bd61e3d1e8b9848e4b10b37',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '44b70f34caeac3fc07e87bcce4c02b871b598e706450e768e273c2fb6bfefb0b',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': '16fdf9a641f1c05edb2b14262491a68fa664c5fbe9c81ea8d1611b571151db1a',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': 'c9fd82b8dd967ee08771491b4dad695a82db9dc010326227a3a6e528f837c1c4',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': '43579b3257669e1f2dbee179bccd8cc0618b0c2eec0c0ce914104a5609d417f6',
 'src/trading_runtime/strategy_one_management_snapshot.py': '4a114cc9d2a7f82000b518b1e89ec835cc0c9b270ed67f45ec315a462ba4555a',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': '9037a91b71561a7b521d56c6428391c88add5505064d65bd6998a06f26e29c12',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '889818ff2a0e10edcfe58f32acab8327e80547413dfae3f7fbb38d23cf9adae0',
 'src/trading_runtime/arte_journal_commit_v4.py': '658edbb634d13c32bdf976d7098ba5f503f3d691579c820c29a64b74e66752c8',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/backtest_strategy_certified_price_break.py': 'f3a28484ef0937b5f3e585c5ee8a6b4c12373c3082c362c9227ad7abf652d9f0',
 'src/backend/backtest_journal_memory.py': 'da93c31c96151883cd04196e956ab28319ac0943c2353b5b717719380d0606a2',
 'src/backend/backtest_typed_projection.py': '1fb9b10d98019e8a2291a8ef5a759fc4aee58995da63dc64c5657878f4a0d759',
 'src/backend/backtest_typed_publisher.py': '7134af423e8b564586532df8993c731761541c5d31883dd35c88d2525f6fcf04',
 'src/trading_runtime/runtime.py': '09e06f9cfa3bc9d6fbe3e590b04fe2787947a3e6be835839e3d1b1d10c38831f'}


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
