"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'bf159304bf65fc3e89705f44bfca9fbe8a97aa2f492957d5b4d80e248e95266d',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'src/backend/backtest_strategy_one_coordinator.py': '45a2194f5bfc8930209ee105d7f833f0d58742be292fed74ba622814bdfa2b41',
 'src/backend/backtest_v4_saved_review.py': 'f2c19cd0bc8c743f3e98a465151d4815aa30afc0d6490d77031cc7951cee789d',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '16a32ff04a16e68def4b357467f9d0e38a4892692c42999319c5ca438bf5f0db',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'b1880c8627c85bb5f8bebaddbf6d4eeec8dead6f6481c5ae7c0a0b8411d2cfcc',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '6f774e48f28f464590a2f58dd3b931f14e1dbf72e7d1de678f8018ad4cfec453',
 'src/trading_runtime/arte_profit_giveback_v4.py': 'ced95fa4696f2a92542113081be3dfea8c9795b94ef23bdd6c8c2b79e61c0eac',
 'src/trading_runtime/strategy_profit_giveback_source.py': '23352e4779dfeda5a49218e6c9aa9edce97bb5288d68367deab0d8b0fba45ef9',
 'src/trading_runtime/strategy_profit_giveback_arm.py': 'cc45018c63f6dda87784686089ab407d9b3e0822751077cb4b296da5a9b4a45d',
 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c871de29c87c01ce3901116bff3275565f6ebc35031145903fbc0e15e0d96b25',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '1b984b97b524ccb198de69e7539fb1b6f7574c4a09e502a24b15869b866e140f',
 'src/trading_runtime/strategy_followthrough_exit.py': '27857445b8c9aefb6b0fa4a32db5026e4ac25625a4d1db6d9acc9b869eaf5cbe',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': 'cd5bc28c3542a01bf9b1ef72245a2ffd7a03bf026f9214b1d2a4628e6865312c',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '21c965ea99dd7eb5df52ae0d5327f6da76dd9ba0a7dea08a1bc4848891f624f3',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '014167e593fbd8967a188c41c469a31f55c1eabe24ee7b6f5c4f710abcd8a95c',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '442a52e13c198cdbadd928ec20667bd8f3f9190842f49d10885daa0ec6ef4a8c',
 'src/trading_runtime/arte_first_price_entry_v4.py': 'aab2828c295f6406999a6ae4097fd96c17aa47ac6763e3345fc81346de555a77',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': '813baf35f0b035ed2076ae3c3d31a18b9fcddbd39a936f35d7334909c9b2f952',
 'src/backend/backtest_strategy_one_execution.py': '1e202198a9d406d398cec6abc3c57019410b6a498a1783aa0c8f52cb1ef1d547',
 'src/backend/backtest_strategy_liquidity_fade.py': '2d93b9ddac2068fc825f713e8ecc1b55471a65f052d82c1ea51e7c2287a68c09',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '814ee7652ae6e64e64328bd9616c4bc24813008c70ce38fb30101f55e8b4e42e',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'e37c681d70b9ceb33d5a9cdff7038e429211f8b7856fbec890a11e178aae33c1',
 'src/trading_runtime/strategy_liquidity_fade_source.py': 'b76c2b5ab4d66f7f9442ae45df2cf43a3c50973a5da7260a78dbab1bed2cb461',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'b676bf72af622fa2081e250919413473bbfe56044326bac57871fd23a004be68',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': 'f243eb25533b27c2b3d7a15032767bf6cf966252746ea9ee79dcd311a3168526',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': 'd73db9cf82446d850817e02aade2ba84fa3925b7364dd6d550753b40f0579448',
 'src/trading_runtime/strategy_one_management_snapshot.py': '9dc93010d611c428a0c76e33d677db5d6cd3c645aa49698617ad1f70ed2fd9e2',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': '2b4935058811e0f1b70fe091691cf0c5a4e9dd95e56e2083cb0f8a81d77a268e',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '7912c05371ae98324b5b03c9df00522d53cc2d0dfd9deecdec93fb5ecb3c46e4',
 'src/trading_runtime/arte_journal_commit_v4.py': 'f07ed2f11699d904c1074704eb231257ae10acbfcb3aea1e83a16f10cd57f687',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/backtest_strategy_certified_price_break.py': 'c55c702d49de21c28873980cb89b04fbf7f576d44d63fc78e649e8ef7c6eaa8e',
 'src/backend/backtest_journal_memory.py': '5c26f11e3d64469f119a2d43c90c0c4295f51f09f9de653423f295de105b4b4d',
 'src/backend/backtest_typed_projection.py': 'a9392473c840f7b3b1b4f4d385b86474c20a3272491610db5b6e610e3bb9cb90',
 'src/backend/backtest_typed_publisher.py': 'b93dbcc0ab68998c19674c5b27c500d5295087015c8235afa3ff85dfe249b8fa',
 'src/trading_runtime/runtime.py': 'e100caac05294d45df561be0d05e25d2da2a0208a4c1ce18d20dc4d216c890ee'}


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
