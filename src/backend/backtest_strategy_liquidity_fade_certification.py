"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '1b93b2f77a672a2f8d7b8d9a25a42fe4ec694412d285c47ef4f57c6dce98ee48',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '8eb314925f85fa5edebd8110c73f5a57868cdf100e3c150f607470f26cf2ce04',
    'src/backend/backtest_strategy_one_configuration.py': '3de29edec7014e49d1501434c586ba5b5789ce5a05d7a452c1d76ca11db558b2',
    'src/backend/backtest_strategy_one_coordinator.py': '6675e437653e32fc691e6402875e5612a6dcfe65d8ce9e41bda64b249a586890',
    'src/backend/backtest_v4_saved_review.py': '9d02e84014a20a2eaa3f2ba4327d7b86c8796ebea11bc519f7985adbaf34fbac',
    'src/trading_runtime/numbered_fixed_strategy.py': '9b67056857a3f0996238ec5d5eac6822056de1efa389eacce80ff1620a768175',
    'src/trading_runtime/strategy_registry.py': '16ea81cdccfe24ba7be1e5533b5904f9e3d389279031a09a4f34688435b22ffc',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'fe2f10d379b99247c59a4dfa2711f8cf79d846e3560405195160d07cd6d046f2',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '4ebe0ca036591de681ad94e435051de37d2cd7bd406a40d583c3e8e8d0d2213e',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '634a59cef1ce7d43a8ed5d16a11a421cffc9412c5612e21bd58f514d3a7c31a4',
    'src/trading_runtime/arte_profit_giveback_v4.py': '40188f2b34484692f6f4417641ccc1fbcec3fb264f02cc05abd47a67b8d01c43',
    'src/trading_runtime/strategy_profit_giveback_source.py': 'd9d33834cd4c28c0dbcfe1474b2173d911633cf3ba179d47d1ddd5a4ac6e9ed5',
    'src/trading_runtime/strategy_profit_giveback_arm.py': 'ab354ff832ec127a4277aadc075218e2dbd6a36853ed51c6aaaeee45a56ee8c3',
    'src/trading_runtime/strategy_profit_giveback_exit.py': 'f32167a2c9582dcbebb66ff27dc80be5b812f45041abb59a0953a92947fd1844',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '0b5101955d8ae4a95b00c970db74bbafbd637fe66ef7c133114ef0a858b3d8d8',
    'src/trading_runtime/strategy_followthrough_exit.py': '23c915754841f191a253d02b80691c84a48aa8deadd0d48709ccb48bd7554979',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': 'f680c2723fe597c11fed56db4f1c241e1d7ae065fdae51b4cd9299c52937b99d',
    'src/trading_runtime/strategy_rising_momentum_witness.py': 'aacc6a8dd498634044d312c6ec1fcd323983bca61e49d3cdfd9a9158a5116a96',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': '33a4c4578da06b77e3d4680f55387d80588b40bd1bf9dcd15f4e66a578f4e615',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': 'c394a09b8cd0ca2b99f177e23be4b3dda770d150e98b806f25f8608470a1ba4a',
    'src/trading_runtime/arte_first_price_entry_v4.py': '68d1bc0d0040d7b54c60f7f9cb104739b2e311d738270fdcd6fe728b58cc835b',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '7a8c2c197736878d61717b591b4fcf599ad05f071ab0834b85ea3cc13f0c3c53',
    'src/backend/backtest_strategy_one_execution.py': '00fad0f28a4f4983a292f2b80faa6f5eea3a4e7299fd83d5844bf02326036ffb',
    'src/backend/backtest_strategy_liquidity_fade.py': '51ec2b5465b638f6311dd519268e8e9302453a2cc34607330df51fcd45d32e0c',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '831313b51b5f766628318af3b27b87ab86daaa998ed3aee4a95bec4ee186530d',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '454e392a67bd9b4d00825e45e78ea461cc02e4008171ac13a692eb99df76d03f',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bc91ba3d4f589593f877593adb3c322f03e909d05ee50db2b621d56b996f8a38',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '265c6d911deb9da31d361b6ffb33bfcc09764c49a8b6c79b634699532746a148',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'dc286798a43e16b3e2680f48661c5285dba3a091d446d3a98fa8d9746f7536f8',
    'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
    'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
    'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': '3d8c3964c20fed559e753609f9fcbdfaa8e9456fed09cc2f2e5c5e4aabf94446',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'c824fad3b87d8dceae518a6e11d5747dccb24e1a170bceea166904754cabe6b9',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': 'd97b8a2c841db218bd6f3c752499d738e51682ccbb38a123ef77c8b4b5d23217',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': 'ecfe7b34ce526228de308ea20b8e9bf89d55734e41ac5c4680781f81859a8b6e',
    'src/trading_runtime/arte_journal_commit_v4.py': '31601f940a7a1bdf844895cecb667cfc9f22a1cd7543f016d3d4fe9a4e113f22',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': 'e4463985da453619128e9690cef4072917cb62662e9f49e90bd09a4964dc49ff',
    'src/backend/backtest_journal_memory.py': 'a163faa0678e524fce4f5e10e9eb98165a59e3f6c31955f770c2be156d4144e9',
    'src/backend/backtest_typed_projection.py': 'c3dc22f7b20f0e3bab12297a00ad009db83957b7ca1617db5d6863a7a868f444',
    'src/backend/backtest_typed_publisher.py': '835a3b666e00f437b002d846ec9d486b1e298db48ed5aedd8ebb5a650f2e544b',
    'src/trading_runtime/runtime.py': 'bb0208c12770110b7461144fff08af3cab5890e4b6176b20af1030e8197ded7f',
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
