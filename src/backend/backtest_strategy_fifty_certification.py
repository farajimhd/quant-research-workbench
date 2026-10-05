"""Full inherited42 weak-positive early failure seal; populated only after all coordinated lanes are reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = ('pipelines/strategy_one/configuration_publisher.py', 'pipelines/strategy_one/strategy_fifty_configuration.py', 'scripts/clickhouse/publish_strategy_fifty_configuration.py', 'scripts/clickhouse/report_strategy_one_trades.py', 'src/backend/backtest_fixed_v4_certification.py', 'src/backend/backtest_journal_memory.py', 'src/backend/backtest_strategy_certified_price_break.py', 'src/backend/backtest_strategy_episode_activity_source.py', 'src/backend/backtest_strategy_liquidity_fade.py', 'src/backend/backtest_strategy_liquidity_fade_loader.py', 'src/backend/backtest_strategy_one_configuration.py', 'src/backend/backtest_strategy_one_coordinator.py', 'src/backend/backtest_strategy_one_execution.py', 'src/backend/backtest_strategy_one_management.py', 'src/backend/backtest_typed_projection.py', 'src/backend/backtest_typed_publisher.py', 'src/backend/backtest_v4_saved_review.py', 'src/backend/replay_run_service.py', 'src/trading_runtime/arte_confirmed_ah_failure_v4.py', 'src/trading_runtime/arte_entry_activity_v4.py', 'src/trading_runtime/arte_first_price_entry_v4.py', 'src/trading_runtime/arte_followthrough_failure_v4.py', 'src/trading_runtime/arte_initial_momentum_entry_v4.py', 'src/trading_runtime/arte_journal_commit_v4.py', 'src/trading_runtime/arte_journal_writer.py', 'src/trading_runtime/arte_liquidity_fade_failure_v4.py', 'src/trading_runtime/arte_oms_projection.py', 'src/trading_runtime/arte_profit_giveback_v4.py', 'src/trading_runtime/arte_rising_momentum_entry_v4.py', 'src/trading_runtime/arte_strategy_one_entry_journal.py', 'src/trading_runtime/declared_followthrough_failure.py', 'src/trading_runtime/early_original_risk_failure.py', 'src/trading_runtime/numbered_fixed_strategy.py', 'src/trading_runtime/runtime.py', 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py', 'src/trading_runtime/strategy_confirmed_ah_failure_source.py', 'src/trading_runtime/strategy_fifty_contract.py', 'src/trading_runtime/strategy_fifty_release.py', 'src/trading_runtime/strategy_followthrough_exit.py', 'src/trading_runtime/strategy_half_risk_liquidity_fade.py', 'src/trading_runtime/strategy_liquidity_fade_exit.py', 'src/trading_runtime/strategy_liquidity_fade_publication.py', 'src/trading_runtime/strategy_liquidity_fade_source.py', 'src/trading_runtime/strategy_one_management_snapshot.py', 'src/trading_runtime/strategy_profit_giveback_arm.py', 'src/trading_runtime/strategy_profit_giveback_exit.py', 'src/trading_runtime/strategy_profit_giveback_source.py', 'src/trading_runtime/strategy_registry.py', 'src/trading_runtime/strategy_rising_momentum_witness.py')

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY50_SOURCE_AST = {'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
 'pipelines/strategy_one/strategy_fifty_configuration.py': 'bde8696f70d4dfd16c12781832fb6e8b566482c4f7c5e2460c62cb3a9634bff9',
 'scripts/clickhouse/publish_strategy_fifty_configuration.py': '070b8ce29c010b6ebf9448510b916ed97a186838e8b9ce048ae6694e43b4ac07',
 'scripts/clickhouse/report_strategy_one_trades.py': 'c8c52c781468a4b442d0df933f706b0d0e400cbe70537184ab2a2b4bb7723fed',
 'src/backend/backtest_fixed_v4_certification.py': 'a84fb97ef2fbdbf75482e78bdba825b9a283cc09d08cbf50f51e77af7f1d2489',
 'src/backend/backtest_journal_memory.py': 'da93c31c96151883cd04196e956ab28319ac0943c2353b5b717719380d0606a2',
 'src/backend/backtest_strategy_certified_price_break.py': 'f3a28484ef0937b5f3e585c5ee8a6b4c12373c3082c362c9227ad7abf652d9f0',
 'src/backend/backtest_strategy_episode_activity_source.py': '9f87032a7764347d25a3c86db2e62ce10adeb0a7debda994432c34212caa9465',
 'src/backend/backtest_strategy_liquidity_fade.py': '3769a7485d858aa74c0ec4b08a4cebe9a67ecacaac1f967e008e2be310abd174',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': '16fdf9a641f1c05edb2b14262491a68fa664c5fbe9c81ea8d1611b571151db1a',
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'src/backend/backtest_strategy_one_coordinator.py': '79063bebc5d92af4e1704c6944fbbe2b81a2ba3780d8dc8de37dc21688bacfe1',
 'src/backend/backtest_strategy_one_execution.py': '6891230fd947b41e708aa458ce6563202135f867dc12a572e0a9f5f40f01b83c',
 'src/backend/backtest_strategy_one_management.py': '43579b3257669e1f2dbee179bccd8cc0618b0c2eec0c0ce914104a5609d417f6',
 'src/backend/backtest_typed_projection.py': '1fb9b10d98019e8a2291a8ef5a759fc4aee58995da63dc64c5657878f4a0d759',
 'src/backend/backtest_typed_publisher.py': '7134af423e8b564586532df8993c731761541c5d31883dd35c88d2525f6fcf04',
 'src/backend/backtest_v4_saved_review.py': '0645e6e3dacb127c3c50e8afc0bae4a867916822f3fd9310b60d8ad6a1c85928',
 'src/backend/replay_run_service.py': '4291f16739779c5bd880a1049bf25f736bb32799b321770b6526e41dbe6c7035',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '6973202acb92d0c89aa93d62204fc26830b5f7d3acf7b9cc5c4e1a7198bd2b04',
 'src/trading_runtime/arte_entry_activity_v4.py': 'a0dddefe7d64b2486f9dc5c22aa88981ef509601e03e1e4543a1b338bb1a491a',
 'src/trading_runtime/arte_first_price_entry_v4.py': 'a59674ba3faab920ab6666a90eb68d7a104d9e53d30303f664b7b2ab088db04d',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '30cd34d45f46c8e05ec7db910c6dc4eeed6691cd99c8f295534df0addfb8b235',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '17b8739a4a3d678eb2ef3f31e549d1a4b23b2b4260a40df519ec28b0223ad3cb',
 'src/trading_runtime/arte_journal_commit_v4.py': '658edbb634d13c32bdf976d7098ba5f503f3d691579c820c29a64b74e66752c8',
 'src/trading_runtime/arte_journal_writer.py': 'c9fd82b8dd967ee08771491b4dad695a82db9dc010326227a3a6e528f837c1c4',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '81045a270da89931b778971c2e974c219a2753f49bd61e3d1e8b9848e4b10b37',
 'src/trading_runtime/arte_oms_projection.py': '9037a91b71561a7b521d56c6428391c88add5505064d65bd6998a06f26e29c12',
 'src/trading_runtime/arte_profit_giveback_v4.py': '1707f58e93a71120e813ace622581b4eab5b2bff79c9a374b4fe5e4460e18299',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'a6d3715486ad6f89a66fd2d5e2211027943acc92b816d8f0cef8721122d128bd',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '6b962b8a200288cbf6094b2339033ae3c31c67c42c03dc1f9edb1d1bb43304fe',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
 'src/trading_runtime/runtime.py': '09e06f9cfa3bc9d6fbe3e590b04fe2787947a3e6be835839e3d1b1d10c38831f',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'b3da5e8ac6d91f1ef26dfd5cc736a6325d89f8283a837a014ec411ef688a45fc',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'a5fb97528e058d5d0fe62f68ed5f8301dc7d671be33ab18b7acadcca1a49ca12',
 'src/trading_runtime/strategy_fifty_contract.py': 'de010651cca50ec4995d75c26a03c2818cae65ca7a8b998ef741a91bcaa9a1a2',
 'src/trading_runtime/strategy_fifty_release.py': '2f078566963e645c725d14526621e87a99d623f415fa010aa2440921e8d87f1b',
 'src/trading_runtime/strategy_followthrough_exit.py': '3e2dba19e2d894550c90f1de1638e7f857eb0ba97c22b6509aed938565ee47b9',
 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '00e678d1bb7960a43ca3fe3ac2d6e975273a84375a0ac82e8191c436439b1101',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '257984b2c296ffb272d7f4d8bab2e3501a667702b2226060756d9f84e2981537',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '889818ff2a0e10edcfe58f32acab8327e80547413dfae3f7fbb38d23cf9adae0',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '44b70f34caeac3fc07e87bcce4c02b871b598e706450e768e273c2fb6bfefb0b',
 'src/trading_runtime/strategy_one_management_snapshot.py': '4a114cc9d2a7f82000b518b1e89ec835cc0c9b270ed67f45ec315a462ba4555a',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '550d2c398f65237b70d353f2a55cc2ed37b9aaa74f3bf08aedc76397f0ffd18b',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '860e2b154c698d088432d85ba7db111fa381447c1ecbb25c37d895005032e42d',
 'src/trading_runtime/strategy_profit_giveback_source.py': '3895904b5ab3e86fa55923ce60fcc2a1dbd5cfce00d34d09e27cc71039740831',
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '38a14626b96cb1076c7b519e8d720c75344797dc81abef4a744452a57db16338'}


def certify_strategy_fifty_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY50_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy50 complete source review is not sealed')
    if set(overrides) - set(STRATEGY50_SOURCE_AST):
        raise ValueError('Strategy50 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY50_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy50 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy50 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
