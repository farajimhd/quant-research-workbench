"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '78eb46e3b6e162bd70078c580d52e611811907ba65f6c71093e41ee2f583881e',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': 'b6a7ce44ff0729b0eac8aa0dd0f2eac355661b9c1a76f1d94f159be28c5813d1',
 'src/backend/backtest_strategy_one_configuration.py': '9fc30e92b34bb1690625dd63a3cd18bd0ddfb89497fd1df33c67bfec804fcb21',
 'src/backend/backtest_strategy_one_coordinator.py': '9549d626e2220efae49073992191cbf340630e28d971b5014cf5d7b10ec089cb',
 'src/backend/backtest_v4_saved_review.py': '77202aaa305d24f0b5689f3f01e2d57d4f4813b00dc8b7a30590760242139418',
 'src/trading_runtime/numbered_fixed_strategy.py': '59e01acb2138459dfa819c6853b8096688c15d9307ed03e2b1e841ebe01042ab',
 'src/trading_runtime/strategy_registry.py': '56a9a7e178c7c062e7948ba495621e250572c15bd5063fff960e8c41a5ead0c8',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'a87bfda24f4eee54568309bdb2c7dfe8bf3b229b47c03e2d3d9774cdb9ce934d',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'a5cf80da21f0e9f54568b1ee12e659bd6fa16158c05688ffdbde46d21b27a5ae',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '929cd5a95975c1fde5cc2662b683931fe4a0db68d5c61e45fd88d1ba314f5133',
 'src/trading_runtime/arte_profit_giveback_v4.py': '233e9a504c592d1da4c447e96957ee3d59c114aeed3d6e8da48b9b9ff1e33aa4',
 'src/trading_runtime/strategy_profit_giveback_source.py': 'de1752c005b04c809b42217ba808792d27ba8704f9bd5aec01cc3f7eec26c2c0',
 'src/trading_runtime/strategy_profit_giveback_arm.py': 'd30d3c40ec0da65f76eb964d9b78c4d55b9e78ecc31e9c0d70a8d88adb5773b9',
 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c707b977dd847c82f8ed0acfa743970ce53a919ef60b7d06ec432deaa623b9b4',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '6601fcafd78180d4eec1141df586fd918177db44b079ffbb6605e70885f090f7',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cfac0ff13329deaa3d42cdbcd44035c46f52a1a9d00ea9649024a629b2dad4fc',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '1e46fe8243c97d4aa09ca60f219005b5cac6c01cd404329647f4c881a0ff9ae0',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '99701a1ba8c4aec27526b564181e9b9930e33ac36453d2012ed40c60bf6d71f4',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': 'bddc5425b37fc8ca6bc8511a208b28ef60b1cb40053b74fb43d021173aad5092',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '7796b61aa232d4ea1b454ac3e941a7e80d6e5877fb252ea4d788d58f0772bebf',
 'src/trading_runtime/arte_first_price_entry_v4.py': '7d96d448ed90668b02d6af42f691c0ac77b7e3c2f94ca3581e85eafb529866e9',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': 'c87e6edf96b94a7f86c620f01ce647d128e57148de554d6b0eab5173f2e77178',
 'src/backend/backtest_strategy_one_execution.py': '37eaef4da4a0f6503d5d98c33ab560df14b975f1ae571249171f55ac1631fab0',
 'src/backend/backtest_strategy_liquidity_fade.py': '897e6e65c63f19b9a741b2090bf291905d833bce13c7109ca290d725bbd9fa83',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': 'a0eeee7320bee01f9345270ad644545fcf4fb34c74b84887a11e8e2055ea624f',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '261ee5afeeb080108c454f43062749e0f6cfc47c82e70a0fea9aac1a2427cb46',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '41987b85e9abdc3969776901a4b9c113dc62f4dfb70be2f5c0ba3b33ca0db897',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': '5822a7fddf72f1cb571fb44884af618498fe227e65d0d7801df1e3fc3b7c646f',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': '0057d79ac2d010ee112413c323404ffa8633e6b3879f90f892e6a92800c5c77a',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': 'c4d3722667eb5024f09086f5e65ce258361028993a65eef2de6af69ff4f645a9',
 'src/trading_runtime/strategy_one_management_snapshot.py': 'cacbd10bd33f921f74cfe31926a8a51ab53f6f98c011a53444dd10b901a3104d',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': 'b31004ffbfa21fb7a697e89031911a853175033597903ea9e2ca2891ccebe35a',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '0571eda28e18e37b2b149dbcebeee0f03848d26e0c8af362b08001f55b55d718',
 'src/trading_runtime/arte_journal_commit_v4.py': '21eeb5914ff32bf6f2efa95fe1c3a3bb8deef581be6d9d350a951f70f20bcbc7',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/backtest_strategy_certified_price_break.py': 'c65262cd6ed9af7c71527994d66c8cf38dc56dbd3600973804c22fdb4caa9627',
 'src/backend/backtest_journal_memory.py': '5bf8e6c016b00b7664f24c26ab35f5e977d71595548baa529fef3a0de42477f6',
 'src/backend/backtest_typed_projection.py': 'dad68574a6ca57515f41fa37898cf6023ea764a3bb93cd6ceb4b09a14503b20b',
 'src/backend/backtest_typed_publisher.py': '7030b248806e7d009f0a78bcb1abd47faec64444fd1779367d4993cbddd74bc7',
 'src/trading_runtime/runtime.py': '4dfba1e0baa9cebefea0d1625062debaa1b477ada24845a2adcdd10d885ffa30'}


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
