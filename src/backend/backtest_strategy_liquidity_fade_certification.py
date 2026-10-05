"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'e5a16376fb84f698afb8ef13f5a59b9af75ccc5aab8836cd7b69faeaab0028b4',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'src/backend/backtest_strategy_one_coordinator.py': 'e3f87d0517f559cdf90de7407092d3aad20e0a622cb551291257dc28f791b481',
 'src/backend/backtest_v4_saved_review.py': 'a66e597fafaebc7650b811b83f06f6f2092136e41a21e560313492937c162389',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'f1fcc57eddb5722116e49acf7d4a2d815d8ceb34fbbe10d44b0699cdc66c3231',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '9dc51903b6016a819f2c457d84872bea440ed6ae20a2b355fb97da83229c0f20',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '22ed83b6d42e085a8b8b59ac40fe3a3ce06e29e9fded220fa8d75e89a2bff66e',
 'src/trading_runtime/arte_profit_giveback_v4.py': '9486fcfd61799446b81abd8e00550e0858bd6c197cb09ba209c0735261d7cd9e',
 'src/trading_runtime/strategy_profit_giveback_source.py': '2c278cd711240f22f24a491231bfc49a2366323a966f4e49f1c9d900a1a98ac2',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '19c91475dc45da258bab57f6f27c2dd7fde8fdae3448f3245c0ac83ed28525d6',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '62f82e3e86c45eee06f382b17e0741c44374810688e2ff17207b72750abeddc4',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '5e69522f0843ec8e275a6b8ef8066609f72e082391e76089cecff7dd66c042a8',
 'src/trading_runtime/strategy_followthrough_exit.py': '68ede578ca6668621ba44b7c9bf1c298f8c963de8bd05a3786942e2de4a3447b',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': 'f53c55086ff39bbd10e99568c1b50eea649dad2850888b8bf68d0bebf6a35e90',
 'src/trading_runtime/strategy_rising_momentum_witness.py': 'b2eaa49ecc1cd062e02a44fc37145b1fa2f72daacdbffce6c08e450a4fbf26a6',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '6d7fb1625517c6a48c0cedbda15ffaa67037237defa7e8e0456dc5b5ed56d692',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'f69d534bf4344bed1cd946fab9d8d70716ce6b3ad8d6792dd7acfbbc2c2d8a59',
 'src/trading_runtime/arte_first_price_entry_v4.py': '72076e003f9cf6f70af5427372d3922dd2ffe7f59171050de723f1f99eb3d96c',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': 'a98a1067c73fb30daa6c17dfb4c07aadf9fb253ad595da32af8f373173fdc215',
 'src/backend/backtest_strategy_one_execution.py': '4dfbb938081cdcdd651bd2a148f73e498338df141a20f162bc5507e0e3c77852',
 'src/backend/backtest_strategy_liquidity_fade.py': '299861f1a5980e9c3a2771f26e22737052b2b3059735169adc96690c38ea1ca7',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '707a5b99826c04e7c807cde9f7d9c494a57f1612495a04dd2d73592e22ad63d7',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '5a7a513a712f46b76049af7290fdbcb1f04d96d580d0861db4d76239975779f5',
 'src/trading_runtime/strategy_liquidity_fade_source.py': 'dd44d3ae2dfefb914acd8aa1f8dcdc3a40625349dbe05ca9d387b4d86ba732f5',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': '67bc58b3ce70907e71cb26ffc9dd4b928da4ce30f7a65625c3b6c1db3c93e5d1',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': '6c7b13c39de95592840b2224c202bb0b00e996076612bd6514e10b6c0d93204f',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': '5eba2fbe247ced68f8d62ff653ceb68fa1486e8e0dbdf00140c88f32604b10c5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '76102fb098cbd89d47ec86849597aae7b0fc65f79263ace8455d30d79c43eb73',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': '12d74f7a51be8ee01483ef8f0147b9d77fb878823c6fd6f3b4757c4167b99caa',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '615203cb7e80b42a0c179ee21bd0cecab5349d1cb4c0ef4254716f98b38b8df5',
 'src/trading_runtime/arte_journal_commit_v4.py': 'c1f3fa36c9e782ba6badb204b85ffe58acdfe044e40f52228c22b2e6aef5c3eb',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/backtest_strategy_certified_price_break.py': 'c11b20c8ed88b2d3350da6a494d5209176069507ae2f6a47a4529f9baf50b6c6',
 'src/backend/backtest_journal_memory.py': '717a5e4a4f5a31806282b3f7b37ba88c04e3862cd8413d49ff0f1bf79bc1277e',
 'src/backend/backtest_typed_projection.py': '22ab5cb4116e3509b27c882a6234adb16e38e9853146014d9cb4b3eca4bdba68',
 'src/backend/backtest_typed_publisher.py': 'a211e08727e013d4429582960c2e9c60a9d82348d4bb2248aaf2a189ea864be6',
 'src/trading_runtime/runtime.py': 'c932991d4183d8dd42999157e8370d5c0e57ce411736e8de45ce1f64fc740a2e'}


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
