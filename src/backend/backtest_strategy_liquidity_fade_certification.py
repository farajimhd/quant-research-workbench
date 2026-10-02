"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '9414a13f87514713bfe8fa389881f560cdd73bf507dcb77f3055a6c3dfebda95',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '451c9ffbc8ac2f0a79be23f0dbecfb043cb840343b18e1fde03ec8feb3fab0fc',
    'src/backend/backtest_strategy_one_configuration.py': '07d3131898357c5badefbd95612cb784357e5786c9194ad006bb5bfb9773d4c6',
    'src/backend/backtest_strategy_one_coordinator.py': '596d97ff1207be94508c1918b108bdda83f5dd2930d7aef7c09114a11a4e1ea5',
    'src/backend/backtest_v4_saved_review.py': '9acbef7723c4874d473a7bf3b2e952fad7874ccba801d7607ed7c638503fd950',
    'src/trading_runtime/numbered_fixed_strategy.py': '334007da600878df78cca37e06fda2a447f34f7c56a7b71b7b912cbd275a82e4',
    'src/trading_runtime/strategy_registry.py': 'f0eeecab59f478c0643e7d443b0b37d5cf4dcba0e1a53989f417e87bc38239aa',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'a109f3ecd87713ae99db0f6efb180659f7a57f6647482bea943c3509c9229b26',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'c2c7249b9961f2d2a1e58a9f1c510204ba67cb1fa98cd9ebde3c98e3f40145d0',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'fce02f0e0b4958399cf08700694bc8d499ab3a86a7468cf06d4ddb96578ab609',
    'src/trading_runtime/arte_profit_giveback_v4.py': 'b7c1c45da5116512150ac7d97875b91a270d7bbf926e2c760dcae221a00999b1',
    'src/trading_runtime/strategy_profit_giveback_source.py': 'eb0f873f752a930f8e43fb8a6d96b172bf0e8c6adcc5a19f2d6f007934a5a832',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '0015308b6058f3938e4b4abb6feddca251ec161e2225f6e0ed2d3fd30e60dbc3',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'ee1ea9f934f60c9414cb7766780064915a42160cdfa50db44959239b88ffed18',
    'src/trading_runtime/arte_followthrough_failure_v4.py': 'beab9a89a9b900e9ff97128a5977ac24a728ad824d9b0a75cc584d22f7ab6b6e',
    'src/trading_runtime/strategy_followthrough_exit.py': '9ea49aa3d647afde72f45d3b9077ea0f58406a275f93d8e3695a37e8c2a09917',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '99ea535c7855ad154273ace89ea69d559170649358a45545c1b472dcb8e5a59d',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '44f937938ef6a87a0812e2fa602253232ab82d3ce49ccfc8b5ea684052f70a52',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '8ee6791b7df8d5a06626241ffc07057accb0e555677bebca8d3b6169cddc808e',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '9f3b80d5dc2762b07149b21769d1babce2c7156b49478c5fabcd640a00611eb5',
    'src/trading_runtime/arte_first_price_entry_v4.py': 'ad2a00154d23e70526bb5195ee2e020243b2281de10ba92a18ed5026131f18ee',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '74b73beac9e55608ca668d70ddc21e6dcf7a9a7798868077cd4ee8d60d9427b5',
    'src/backend/backtest_strategy_one_execution.py': 'b924c30ac06bb2a12edea845377e31ad5269154296ab7207f532ad0f38b12a80',
    'src/backend/backtest_strategy_liquidity_fade.py': '0d30d0d0c00c2cd4f3f0edeb515bc90e6354876d653228addceb12fd2281d6d0',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': 'c23adc5dd3ddb3f9fb7379a75386c49363b15a764318b772a7982efe7a328307',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '917d96270d63a82f22d718757826cbfe31eb8f96d404e092d391af13e4db0658',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '84c350efed49fd6d2032ba0e150a10e7141e86035a0d48c1690d02bf64ed12b3',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '71fe3889843508d9987db5d0b15a20d74766c22d9463760c1ca5bf2556d19a68',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': '2bfe43dad14887e5684b6e9a0c896886648aec8b3584e7506b7b2d16f3f8a26a',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '4eb215447f0ac551c02cf7a6f5d357be5053d71ece106b3b5548947f97f62448',
    'src/trading_runtime/strategy_one_management_snapshot.py': '0b3d19cc50cc65f5cc011e3f2357adc015b73cbe32788c780c4b15b245b9aba7',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '39cfacb41bc4a95649c710440600723079719a1c9dbecd5384228ca6c9f8af2b',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': 'cacd2d7fe76bfd74c9f79ca0928fa46ea0d5d9f63ae016fee837813f47ad11cf',
    'src/trading_runtime/arte_journal_commit_v4.py': 'fb1a530fee5999b964175c598d9c6b2d15326841134dbb5042efba736430ced3',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': 'c46680005979c91006d9ee48d47c04631fd513cec69778472dfe6ca5dcb03e58',
    'src/backend/backtest_journal_memory.py': 'fcebb6b414584b0cc596e9e8bcedddfaa1f7fd3d1ab1b68e265f226386ab74ae',
    'src/backend/backtest_typed_projection.py': '05961685789902af33cc963c49287b09b759cc56d82ffbcb8050264e72629501',
    'src/backend/backtest_typed_publisher.py': 'b7081eb57979fe3472a40f37c0954c4febbe6c205203941ee7177c9ca23dc32e',
    'src/trading_runtime/runtime.py': '6394a5a4f27196b8b8be50fc63c6734f11b03af946b3c142999c304f168b5986',
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
