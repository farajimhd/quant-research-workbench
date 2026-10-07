"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from src.backend.source_ast_summary import canonical_module_ast_digest
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': 'ea1b79346b0f3b4b09d5fbb2439ed90bdddb2c29a550f40ce25e5545db71c5e8',
 'src/backend/backtest_strategy_one_configuration.py': '7205d641da91326b4eb8000225cff9bedf3357256d055f005ecd262bdc56d1e9',
 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
 'src/backend/backtest_v4_saved_review.py': 'f6a2048ea195b7a39e6694e89cb7f164e4d83826197727ee90dceefb5e852ce9',
 'src/trading_runtime/numbered_fixed_strategy.py': 'a6a6147f4d001a6217512a74cc35fabd51918984eacd43e7800b4ba2ba94dd0b',
 'src/trading_runtime/strategy_registry.py': 'd6e7c467ad1bb61c700a05bcea4bf1f36883bd987796f880eb8db7e12943ba3e',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f',
 'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293',
 'src/trading_runtime/strategy_profit_giveback_source.py': 'b3e58e1bf6cbb1f77a79ddfd41a8ce7115c634dc80753877549f37947db75189',
 'src/trading_runtime/strategy_profit_giveback_arm.py': 'ef5b2892793ecf7ef2d6957a0851c1c2010e1d4da5c21c7e0dbc5de46fc39f41',
 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '2ce1e6805a9db71fa5afe0ec63aba61a7d87fc95e3847b89bd7e94b93aa1bc6c',
 'src/trading_runtime/strategy_followthrough_exit.py': 'e5ed9aa87f606bed29e550756d11e7fa1445d9ffb47f69d4f1402bfdb17b9984',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856',
 'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea',
 'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1',
 'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d',
 'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134',
 'src/trading_runtime/strategy_liquidity_fade_source.py': '5554b994c4ac8c566a1883c676c3c01206ee9d091ba0e10dc2c7c81890f126b2',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '5d08bb4fca7c8af5ae6ef711f16f99fe38cca561c46207142bb06640cb810d93',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': '6800d3df81562539ae5dea1ed12474f298f7a3dcf3cc16985567530601d342f5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '3f239ee12ca02793e6bf2cce4c5a809605593e0f059aa352b32fb4ae5314fa18',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '1ff8cda4665f3fefd70fef5a56ccfdc7cf77a55caf819639f17e38aeffe36739',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': '1c4571237c9231fbe1660349be684adaf52a2b34d19b4c49c9489af9392a1a7c',
 'src/trading_runtime/arte_oms_projection.py': 'e0accf89d43ab445f0d0520d4b4b811c86381b540043fc1be9ee982dd419e25f',
 'src/trading_runtime/arte_journal_projection.py': '9c78a31a6bad5fb1026053f0b5552c9ed37bf717c75143da25f14d9df3e08efc',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827',
 'src/trading_runtime/arte_journal_commit_v4.py': '980a2719498df8ad83dfd519b105d9a06bf9164467b42293fce107ab3ef02027',
 'src/trading_runtime/arte_journal_compound_v4.py': '05b28c131df86e9b743117c1c801eec7384995b0bbe8c8b80c9afb53e183eb4c',
 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38',
 'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33',
 'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e',
 'src/backend/backtest_typed_publisher.py': '89ebb43227635813d78c07f3751cca2f93cd095c26cc1f9e5b1f809e3ca2da61',
 'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8',
 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c',
 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81',
 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
 'src/trading_runtime/confirmed_original_risk_failure.py': '738493dce849c08fd7e1d4f5c24a7c3188bc772a183d9083a86713177aba6f39',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py': '747bd14c7cd27adc1151770c7b946d6c14074a2e813f167dcf7e4241604176d2',
 'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
 'src/backend/backtest_confirmed_original_risk_source.py': 'f51caf0f2197a606d02de3972aaeb41b9291dd487a2549fe2fd9987162bfa859',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/backend/backtest_declared_ladder_plan.py': {'automatic_policy': 'f5383587a2b55fcd05c6c6f0872d7e641b1059d6ee229094f5a10ef15b5c2669'},
 'src/backend/backtest_ladder_source_authority.py': {'declared_ladder_policy': '56c0bef2dbea0bccd752d45293520c1ce8ebec0b3e2829c48c5a2a7eccbe360c'},
 'src/trading_runtime/squeeze_ladder_automatic.py': {'AutomaticLadderPolicy': '8e6b530d31208cc0c139f51eee83da902e67a16f532118b6f7eacc90a30f936f'},
 'src/trading_runtime/original_risk_checkpoint.py': '7e39fb2e7cb189ab080b7bb46e53563c73fbafb8c349f23ff1ca5be1fda3af07',
 'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac'}


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
            from src.backend.source_ast_summary import canonical_module_ast_digest, canonical_symbol_ast_summary
            classifier_symbols = {
                'src/backend/backtest_declared_ladder_plan.py': ('automatic_policy',),
                'src/backend/backtest_ladder_source_authority.py': ('declared_ladder_policy',),
                'src/trading_runtime/squeeze_ladder_automatic.py': ('AutomaticLadderPolicy',),
            }
            if relative in classifier_symbols:
                if type(expected) is not dict or tuple(expected) != classifier_symbols[relative]:
                    raise ValueError('Closed classifier source selector shape changed: '+relative)
                from src.backend.source_ast_summary import canonical_symbol_ast_summary
                kinds = ('ClassDef',) if relative == 'src/trading_runtime/squeeze_ladder_automatic.py' else ('FunctionDef',)
                summaries = canonical_symbol_ast_summary(source, classifier_symbols[relative], kinds=kinds)
                if (len(summaries) != 1 or len(summaries[0].digests) != 1
                        or summaries[0].name != classifier_symbols[relative][0]):
                    raise ValueError('Closed classifier source symbol changed: '+relative)
                actual = {summaries[0].name: summaries[0].digests[0]}
            else:
                if type(expected) is not str:
                    raise ValueError('Unknown dictionary source selector: '+relative)
                actual = canonical_module_ast_digest(source)
        except SyntaxError as exc:
            raise ValueError('Liquidity source cannot be parsed: ' + relative) from exc
        if actual != expected:
            raise ValueError('Prepared liquidity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
