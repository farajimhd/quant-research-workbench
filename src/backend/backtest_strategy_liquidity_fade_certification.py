"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '1c23a49c0833ecd0c362949f63b45fcab1050dc122ec27671c84623ef10b4e4d',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '571e90ade71025ce0fb71cea99895c0e20145b12dbefc1b2398efbd7a2601cbc',
    'src/backend/backtest_strategy_one_configuration.py': 'd75eed6ac5a3404930474e524bff5f905ee4dc42cae83c6d27444a50f56b45df',
    'src/backend/backtest_strategy_one_coordinator.py': 'b3da0885371cca61702e03110614a7fc1701edfa779a577ea1ff35ef75881270',
    'src/backend/backtest_v4_saved_review.py': '1989dc13a2fa82063eb1e1748f1b46e842d73e7e888867555d85552665cd5639',
    'src/trading_runtime/numbered_fixed_strategy.py': '4572b8612247111a011c396f39ef14d879dca99b6988a81b0cc5a60f9103e861',
    'src/trading_runtime/strategy_registry.py': 'd6b06838efb9d86db9e8ccf258c0d434913a28a583cae0dd7bfa9dfbd4bfe842',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'c3d275224d5992341e5d9ff3d18725467a314c01a90894a489ccb885c4f29ea4',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'b844cedfbb139407b877d2afc49402b8db8311226bd82fe0d3eb0d785e0a9b9a',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '25cec45a1ac29c9f2aebf6df39f4530548e7fe0d2d8a5380fd70c01651b1e2c9',
    'src/trading_runtime/arte_profit_giveback_v4.py': 'fa8b8963889c932750e2d0505a205e3253d8f884cca1d8d96fdb457d191e03ce',
    'src/trading_runtime/strategy_profit_giveback_source.py': '75f18316301b3f1e41b39c72923d6ee9df3a4841150968837698334d9d9823e9',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '0c76d4c4e8e9149375f97e2aaf42fcee03c53c3138a5a6708f9ab8308055a81d',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '55f5acdf1e51a77356434ab95b4b57caaeef37ec561368cbaef3de0a11078011',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '8f3c9c6bb881887c7615937cff197ba190b2857749b0105e4eeb237f57e48b3d',
    'src/trading_runtime/strategy_followthrough_exit.py': 'c61923344dfeec04f7aa063b26d8d1900c9f4e12066691d65c313321c62c127f',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '46c4915dc9f07a4abe7e36954384294f5c12ca0480828071030a7f40070b961a',
    'src/trading_runtime/strategy_rising_momentum_witness.py': '67cd78a79f7e2201900f6dc52fb7f35a4039bec40089d7f34c0a7c9d6c030d84',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '1fec1930eaa5b4dd5965d6d8ac551c17642c9c8d9e0f6ca1afbaaca9ccae3de4',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '1712aa7803f7f703c18454505524e6b13eac09ae5631d15b7c676dd3bbe1b42a',
    'src/trading_runtime/arte_first_price_entry_v4.py': 'ccd5a61f6bd8b8cc0f8534f11601970939a53e9cd2a43d2f028723d2bdf6eb84',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '61b65108aa9636197b6428acfb8a63dceb8dfb9cecca46374fbb1def864afbf4',
    'src/backend/backtest_strategy_one_execution.py': 'feffe123c7935ee493cdba181474d56d078a3f4f525b980a1a3151f538c96d04',
    'src/backend/backtest_strategy_liquidity_fade.py': '725163df2dfcd151bd6f325b82c5195845fca44f3f3493b068e6d6e42b04339b',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '7fc1d4048e70cf54d12f72e8c2efd3bf80494a188eed84bda953f2a76460847f',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '17f8a371a9950bae3721f3bdcc4b7ce97623c7455a212fec1fc61c28088c9ae5',
    'src/trading_runtime/strategy_liquidity_fade_source.py': '479499c709645978444901b186f9acc506111afef79da95170763f21cea3fc41',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': 'db7e3b9f0c77ae028cd7506b73e505e19c90ed74999d3f45876b45986749458f',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'abff555cd4be1eb812ebac563f6910772f00a18c98ab0d7c537c3e405b12b83e',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '898697d401e8391cc7b6a83f19044365d9e49535ec808239f8ef4549942b2959',
    'src/trading_runtime/strategy_one_management_snapshot.py': '47fa9cda2196e01c1e30840fd2944d409700fbd71c240caeff161c0f1f32f54c',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': 'd97b8a2c841db218bd6f3c752499d738e51682ccbb38a123ef77c8b4b5d23217',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': 'd2d8e5eb8614bba8a2d1e4cfa6a5acaf6b768f689dbf8738d0e02a29f0a08052',
    'src/trading_runtime/arte_journal_commit_v4.py': 'dd2cd07dacfda6e45e689c8aead1048163ccb5c11b9cdb3bccca986f446410b7',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': 'c29fc5672ee4fba3d38658e917fe582ba043e83c1df04d10036f48418dff72f5',
    'src/backend/backtest_journal_memory.py': '04e99b3a6699dee25a79546ac3062cfbda4291a50a13c71cb7b3a841962b12b7',
    'src/backend/backtest_typed_projection.py': 'ef1af5aac44be18d27c909fcd7bc91bd6bbab14fa2f9da18f47b61dcad95a432',
    'src/backend/backtest_typed_publisher.py': '3570e265f99450c7993d50097bc7fa354eff8021355f6fdf41d1c556c44ee664',
    'src/trading_runtime/runtime.py': 'c159dbf330294abaae988a5e59557e9dd81563690becafcd5646c64f786eadba',
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
            from .backtest_historical_strategy_projection import historical_strategy_tree
            tree = historical_strategy_tree(ast.parse(source), relative)
        except SyntaxError as exc:
            raise ValueError('Liquidity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Prepared liquidity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
