"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'f3951f72d99b6fcaac9deeafad1331096c3deba510de823f476261f0652fb6ec',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '3d5ad0a8e627ca8f2ebdb64d469e5babbb49fd8fb349a1997a861b27185ddb6d',
 'src/backend/backtest_strategy_one_configuration.py': 'e704996f009d645191178125570420d5ff694577eedac9ff731eea0e4f1d8516',
 'src/backend/backtest_strategy_one_coordinator.py': 'f0ee0c32e7b748bd79c912acc20d12ffa6ba8abda0749f95724d3610e6bd4281',
 'src/backend/backtest_v4_saved_review.py': '8483a39ab5e63f6e71a693cd1341166840a6dcad0e921b8c20b4e68b263c4f31',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9c42ddd6921012fcf3d761a0fd718a8d207df5601d4440b682b40b0769a0784',
 'src/trading_runtime/strategy_registry.py': '65220011cea8e502d82fee5bbaf58d856e648ceace15dc48e2d0e9020e81f40d',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '0a1d41feddb4e7922e9670a6216f6e267f1ec268ddde59e6b09b5272c3cc98db',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '317fa8d3170c2a845e3569085f3269dfbff797f6d26124d3838cefba0cafc21a',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': 'b57af2b4c91d03c3d05537184cf2b1101f80ecffae89b60f7072f731d0bc441e',
 'src/trading_runtime/arte_profit_giveback_v4.py': 'd0959c19a0a239b16c1069b12dd77677fbf660111cb6ad32a77538a10a860557',
 'src/trading_runtime/strategy_profit_giveback_source.py': '07e5c09aa3e556487311fee18beafa327da2ef38f3f0c70d7810bc701985643b',
 'src/trading_runtime/strategy_profit_giveback_arm.py': '7e36464b288c4b12f620aa18136314a68a9089999f884f8554b020dced2ca75d',
 'src/trading_runtime/strategy_profit_giveback_exit.py': 'fb5afe770f45580c9edcd3b1d4de8314d370418d910b9ebb0eef86fb79090dfa',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '13faaeab9e5d3673ebeae7f6b3359a8561516581d35a3530ad9cfaa1ece97228',
 'src/trading_runtime/strategy_followthrough_exit.py': '71716c77e5510472df6bebc06995294dfdfa15686589ce01516c9ea2beacc031',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '8f873b171521ea6787b3ac62e5b080b9dfe1008bc43607e38d2c029caee3d3e9',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '65abe3be45255485fd5e9c63c5a7900f03869f1cc59505e2f2b2d755dd289af6',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'a4b6ced6be9092d39f0885efc5e4af2016747b1ef1b0ad67b992ca103d8703c3',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'c4a3be67f0d90b2c72f618ffdc99e0abd51814389ece601d5f5006210cdbb9b1',
 'src/trading_runtime/arte_first_price_entry_v4.py': '6da544d7f18b16cdeeba99fc630feded1873e1b68565ecf8e26f7cd31e1cb99f',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': '0c5236fa72b87a36dd1675c28966d0431a042590e032fc72f3f055e713fa576d',
 'src/backend/backtest_strategy_one_execution.py': 'a4b08fd1c0ed2bb667bddd221b191986156ca9b230d4ada2578d114cfbd84246',
 'src/backend/backtest_strategy_liquidity_fade.py': '2058fc55bc605877e10bd4efac32e1a5a32a0d6c9b5c05031b61693a48d7434b',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': 'ebff0f9ef01beaf43df30fb599281a3a91bb24e6be03f78073be3f04a5252964',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'e6c07b7944b64070c595050a6b2eeab90fde490246312d3f13a731f73faaaec3',
 'src/trading_runtime/strategy_liquidity_fade_source.py': 'aa3d93448658dda6e5144442169bbcf285359fa1ee376de1080466c5a7595986',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'ca816f8d8587df137fe8c29fa3519d93d93ab46ffa5207c3cf71775a69ab9fcc',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': 'f59200246c39103d6f5efe5d07497975a75110965bb49054b8c01dc428e16c8d',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': '54815ec98ebcc11913e48e2f215261100a14860990908ee2c9fc2467aede867c',
 'src/trading_runtime/strategy_one_management_snapshot.py': '6f3644877c90947bf85f443285cfbe4588fe3964f89caa665ecda36af97fac85',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': 'a2eb28260f581a1e482570ad2f6658c678281c13efe76f84474196399c6749c8',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '2674b2274069fc2b4156a56baf125329597d7c10c3e7da975a08d985578ca600',
 'src/trading_runtime/arte_journal_commit_v4.py': 'f9b75fc7064d357f55dd13a39ab71f7edacd1bd6fdff106c85dc8dec436b17d5',
 'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
 'src/backend/backtest_strategy_certified_price_break.py': '3eef13a9a2baaa85217ccdce64967d1ed4d628573990d524e91a14fd9692eb1c',
 'src/backend/backtest_journal_memory.py': '90aa86427d86940e0e9c345900facbd2fd4faba85a522b1a891f0f67430c6444',
 'src/backend/backtest_typed_projection.py': '1ac2cd9c86680f5e80e32c51603d259a13bf9b027ffe0aa70370bc003216d81f',
 'src/backend/backtest_typed_publisher.py': '021bdc1907deb22d093bcdb44c66ae200e1b5ee1031ad4dbc6eff46340024460',
 'src/trading_runtime/runtime.py': '4e9d7efc1f19c85f2adbe3e1882ef2a552772ca68a78508bde1e612098aef251'}


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
