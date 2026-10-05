"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '07d634f30d5533cc4541056fc3535d65e8c531864f9c430b0c691b810136de2d',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '212d1bd2cca308ae71c4a4c711747faf5ea3cc34689e5a0f078984cd3339a54d',
 'src/backend/backtest_strategy_one_configuration.py': '5ea3a8b17b014e119e6ae181202d815fd8307346b5adf3d694f6538d43c1bd5e',
 'src/backend/backtest_strategy_one_coordinator.py': 'bcb57fe0c67bb308c5d7b00f0ba73a4e10c8532dc1096ee61be217f06e662cc3',
 'src/backend/backtest_v4_saved_review.py': 'a46c530365db5c6234b84a15685fd8d965a38b99a26e03130c2465b961451ebc',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9cdbb5afd51b1d9ccad16e42d89b7380c77f7ecad2c48cd785aed63ecaf98b0',
 'src/trading_runtime/strategy_registry.py': '26edcb6ae79806ee700e50bc3ca771aa5004dcb8c58a6ab6ea3d116feecf4be1',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '7c08e91f0b787ac909125804fbba9fcf3865861226d3466da98d8952e724b0ee',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'd7ebdd0521c11703dabe0b69f87e9144b88e1eea04df86f5765ce031920a2514',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '9d4009ce00e7fda11501206e0762f608ed06abd447beb3a88e9958b14a07c239',
 'src/trading_runtime/arte_profit_giveback_v4.py': '6335a415be1cb3e4d15832f2bf4fbe705e0c5a844519445f627a1f762e191317',
 'src/trading_runtime/strategy_profit_giveback_source.py': 'df6937e04877828447292556fd3b81321691a5e30d2e9e3d9fb639c418c42b69',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '96843a8af2eba81358d598f88cbd089a5fd722ee49efec4eb346c511f9baf046',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '3b8850129e7f98f9c983004f0f7bd202d0f71da17ed4a4cc7fbb1564721216fb',
 'src/trading_runtime/arte_followthrough_failure_v4.py': 'b9f177e75083cb7290b10ca1a64b9f4d0b1e5edefc43060c310139fc9ad3a81f',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cc77e2075c848c57ee85334d4eb55a73a1ab2c8d624845919861a0a6a7f0d145',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '9b841984ba4e2e3369e43c21893ef44d6ba7273e7f31ed3dc22374935706bde8',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '64b812842fc2eabf2f0473a35011db4bd8cd04e2d153cd5e3206a2535fd6de58',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '9139f61398216a7994f144b6426606b81a5020150c06ef16c394b17cb820d601',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '18bb68b6dc216725c73da8a23ee7e924b56f1aa20d1a921d8db9b4a1b1734829',
 'src/trading_runtime/arte_first_price_entry_v4.py': 'f7da1f1a57a2eeb159f3a6e2bb357d46eda513b8aeeb5c31bd9ac1ef4f36898a',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': '374c9019742a4083782a9c6117bcf3b8d697093e6ee8463275253d91f8cf6b23',
 'src/backend/backtest_strategy_one_execution.py': '141e77c828cb552ade7a82fb78c665739b3ca699e8bf1449f8ec642c7b82442b',
 'src/backend/backtest_strategy_liquidity_fade.py': 'ae07c0891df254a26763ea9c5202a4dcf0c77c0ddc8f3e61b54786f597f49a2c',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '413fa6d12e920462f56df4b49e63f02ae4b24d680cf7dd64fca51f27e6528d57',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'c7fb5e48687d3794f0f244f5a54a156475e2798593e16a109077febfd1ed9906',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '059f96302bcf19df50bc256f45f27a6a844ac2b41a7001d6a8b009c153ee6dce',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'e2c214f97323d7d275b761d0e2ce992d59aa9912b3c124598317d8bc77d2ffb6',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': '58a21b514d5443ad135d73869149a3e361ef04199627ffedfa04e64d1e3d83f2',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': 'f1eaa31e0b38d3ab80759da1e78a23b1f239646ffe4d3fd7a4e3c2dacdd5bb08',
 'src/trading_runtime/strategy_one_management_snapshot.py': '3cf52e52b1030e5e4d197a14f84493cbd6534a7d7f99537b37a3f4c644f3086d',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': '4232070029a03c6b26c0ab266ddea4d447595348c0ed2918deb80753f8c91c56',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '8671072da4c173a36c06f61397f1898ea3497c93a23576adcca094917bcf772e',
 'src/trading_runtime/arte_journal_commit_v4.py': '9ec544186b1c180821151e8e96930a3d654e6a7fc9818cb5e7e8847fabcefd8d',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/backtest_strategy_certified_price_break.py': 'c98c595af1dd70906fdb99fc7210ffc1533d86ec31aa7c50606acdab78057fda',
 'src/backend/backtest_journal_memory.py': '961c76570d96b47b086e591701bba35cf0b7e842a674537e3fddc880fce87b5d',
 'src/backend/backtest_typed_projection.py': 'b21dcf3f98eaf9c79b148741d0626f19e6af1eb2606c7d6ceb9694abd0613c86',
 'src/backend/backtest_typed_publisher.py': '1cf4b1011aa004219f4eb8d4398f9edea985eebb1092c4f13a299b65635b8e41',
 'src/trading_runtime/runtime.py': '6794e59df7b88582c32a8ba14009d1dbbcfb0cd172d2a51cb0efae6142457b58'}


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
