"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '2011cbc215acc9608d87531a2e22f93b63a603a056c1bbff04b4dfcb333e7286',
    'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',

    'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
    'pipelines/strategy_one/configuration_publisher.py': '3021ab1f454ea0d74f36623c2f7aaaeafe917a7abb56116d39aa127f02400f5f',
    'src/backend/backtest_strategy_one_configuration.py': 'c29449ca87d14d3b66c4f644c7ba286fdad6423101b4b3f6ccf935c6ae74cce4',
    'src/backend/backtest_strategy_one_coordinator.py': '4ef162fc1e65ad08213d6b51b82a5d71a77627d38d75a5f8f6b3d0cbe987ca2c',
    'src/backend/backtest_v4_saved_review.py': 'f586cf649d22c57d934051cc050eab362e50880f451754a57cb35675c6a5ec78',
    'src/trading_runtime/numbered_fixed_strategy.py': '5d13530df770e50eaca93e3e92450d690a12404e070d93140e5a608117802268',
    'src/trading_runtime/strategy_registry.py': 'afa46ca933c2119456fd0c7aaf9a1d4f1e050aa5e7653583dd09203f3bd401a5',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '446d4356172897c8779b02b6202a9896e2eaf83b65f79c10b74847b3e339b839',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '77cbedf818265305a491bb5f9debb54f44765860dc23f58d6d95fc78dd7f5d30',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '7b88ac4443af15704e7584186cde7f8985e7cfbfc290ef711c1c2d1034c3f58b',
    'src/trading_runtime/arte_profit_giveback_v4.py': '75808647b349bc92b046939362adce43f35ed76cd0767a8463c00aa194f37651',
    'src/trading_runtime/strategy_profit_giveback_source.py': '44eeea0148374e1b3077ebf87cc9d57620d02790e5db45ba5bde4307cc298cc2',
    'src/trading_runtime/strategy_profit_giveback_arm.py': '19a0a5c8d37c4e1247aa41afb3c9320da9dd78718dcd7d8befd4a431a3649133',
    'src/trading_runtime/strategy_profit_giveback_exit.py': '7adc5dfcd35ba5f30796cd9d980ab98d3bbc0aee25b0e95cc9dd5fc46ea8ad1f',
    'src/trading_runtime/arte_followthrough_failure_v4.py': '90a3cb2ae4d4c8b9bd3ff1781307fb0c855cafcb10f73853f42c2bbd7dd2d42c',
    'src/trading_runtime/strategy_followthrough_exit.py': '5873c0e81fdba3af25a350ccc83e08039fa3bff97dc6de1f78f90c8ee78108e3',
    'src/trading_runtime/arte_strategy_one_entry_journal.py': '29c9aa7b3b5855b101a363befbaac1a66df50e501866c601f5485c33f689aad5',
    'src/trading_runtime/strategy_rising_momentum_witness.py': 'be0699521b99bcc51a51b16522326c6db05ee02491090f723dd9a221d6ed7cdb',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'bc7289b99dab230c4e5b4c03ea68407b179df1667015831b95a116249a1827e1',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py': '0371f5c6d947e0848d0bc6c8f31a288ad433abab51d08612953cdbf54e270b92',
    'src/trading_runtime/arte_first_price_entry_v4.py': '07c8a2b45feeac22b75934e2b27608ff89dd512c98dfb46805eb0c783c1cf83a',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
    'src/backend/replay_run_service.py': '630ac301b866f7471e80082f29d5721672bb60ab8ef9ac2e3e429f9e64170605',
    'src/backend/backtest_strategy_one_execution.py': 'eb79647935683952adfb6735e2eb0004e5e418dab1cb1f149813ee29270aff4c',
    'src/backend/backtest_strategy_liquidity_fade.py': '2262ffe427107eafe3d6d2e74431516bb55f5ba401efc0deeac3584b389e5c63',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': 'af069bc7e705adb3d7ffbf70ff45925fd2ecd6a25f3d9f471f4e4dd40291b09b',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': 'd2bf2492b926949f69fb815886cce559a0ae274ea4937eadda5163ee4a52b120',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bad5cf8604b46cc678dadea78aed716a8b828612bc5b45a69c86bbb5244977eb',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
    'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
    'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
    'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
    'src/backend/backtest_strategy_liquidity_fade_loader.py': '2bd26158ad11dcbb6546ce71c0f5d4348d218235421dcbda98a206c8bc958a1e',
    'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
    'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
    'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
    'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
    'src/trading_runtime/arte_journal_writer.py': 'b91b820d2fd5144f47a92fc80a0bd7bff78216f028c499cbbba53fbd7000cc96',
    'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
    'src/backend/backtest_strategy_one_management.py': 'e7a69fad14bba450e08a84ce602c621401d76f9671afdb1231abe569acb65818',
    'src/trading_runtime/strategy_one_management_snapshot.py': 'fb43bc59d173903a802ffcc9510fcde7e6630456f8ee4dba8e832a171e9a56f9',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
    'src/trading_runtime/arte_oms_projection.py': '190d249b80c5e630e359582f1a9852d7f3be55dd0ce3aec7e0443c40cf4d2218',
    'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
    'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
    'src/trading_runtime/strategy_liquidity_fade_publication.py': 'a107f261fd1231ba32f1cb803b51bb1c08b03e5b5329483f57373033751d5285',
    'src/trading_runtime/arte_journal_commit_v4.py': '98f316de4da04fde500044328624aa4f96237cda44ac7a1e76151879e848c25a',
    'src/trading_runtime/arte_journal_compound_v4.py': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067',
    'src/backend/backtest_strategy_certified_price_break.py': '1d6e254ba400f3d17f7d80059e8f4bac36c9f04988d6bde7f9460229e75bfaca',
    'src/backend/backtest_journal_memory.py': 'edf5c200c98b8e2f021d96952d2a79fe5f46e9085f7d580fe0d4f7c7fe605cba',
    'src/backend/backtest_typed_projection.py': 'e3915158079fdace8c1138a19baefe41444933ad17b4d771bfe5972ba5c9da5c',
    'src/backend/backtest_typed_publisher.py': '576119861b90a0931e75347ad81f7465fc1fa4d2542744e817b36dbc9a4e7df0',
    'src/trading_runtime/runtime.py': '96c7b16f3d707bd92a7c47124e8336de9538907109a7b7bb3cd21af69e8a8b65',
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
