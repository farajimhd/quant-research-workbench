"""Full inherited50 and declared entry-growth source seal; empty until reviewed."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REQUIRED_SOURCE_FILES = (
    'pipelines/strategy_one/configuration_publisher.py',
    'pipelines/strategy_one/strategy_fifty_configuration.py',
    'pipelines/strategy_one/strategy_sixty_one_configuration.py',
    'scripts/clickhouse/publish_strategy_fifty_configuration.py',
    'scripts/clickhouse/publish_strategy_sixty_one_configuration.py',
    'scripts/clickhouse/report_strategy_one_trades.py',
    'src/backend/backtest_declared_initial_momentum.py',
    'src/backend/backtest_fixed_v4_certification.py',
    'src/backend/backtest_journal_memory.py',
    'src/backend/backtest_strategy_certified_price_break.py',
    'src/backend/backtest_strategy_episode_activity_source.py',
    'src/backend/backtest_strategy_first_price_source.py',
    'src/backend/backtest_strategy_initial_momentum.py',
    'src/backend/backtest_strategy_initial_price_break.py',
    'src/backend/backtest_strategy_liquidity_fade.py',
    'src/backend/backtest_strategy_liquidity_fade_loader.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'src/backend/backtest_strategy_one_coordinator.py',
    'src/backend/backtest_strategy_one_execution.py',
    'src/backend/backtest_strategy_one_management.py',
    'src/backend/backtest_strategy_one_stateful.py',
    'src/backend/backtest_typed_projection.py',
    'src/backend/backtest_typed_publisher.py',
    'src/backend/backtest_v4_saved_review.py',
    'src/backend/replay_run_service.py',
    'src/backend/source_ast_summary.py',
    'src/trading_runtime/arte_confirmed_ah_failure_v4.py',
    'src/trading_runtime/arte_entry_activity_v4.py',
    'src/trading_runtime/arte_first_price_entry_v4.py',
    'src/trading_runtime/arte_followthrough_failure_v4.py',
    'src/trading_runtime/arte_initial_momentum_entry_v4.py',
    'src/trading_runtime/arte_journal_commit_v4.py',
    'src/trading_runtime/arte_journal_writer.py',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py',
    'src/trading_runtime/arte_oms_projection.py',
    'src/trading_runtime/arte_profit_giveback_v4.py',
    'src/trading_runtime/arte_rising_momentum_entry_v4.py',
    'src/trading_runtime/arte_strategy_one_entry_journal.py',
    'src/trading_runtime/declared_followthrough_failure.py',
    'src/trading_runtime/early_original_risk_failure.py',
    'src/trading_runtime/entry_momentum_growth.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/trading_runtime/runtime.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_exit.py',
    'src/trading_runtime/strategy_confirmed_ah_failure_source.py',
    'src/trading_runtime/strategy_fifty_contract.py',
    'src/trading_runtime/strategy_fifty_release.py',
    'src/trading_runtime/strategy_followthrough_exit.py',
    'src/trading_runtime/strategy_half_risk_liquidity_fade.py',
    'src/trading_runtime/strategy_liquidity_fade_exit.py',
    'src/trading_runtime/strategy_liquidity_fade_publication.py',
    'src/trading_runtime/strategy_liquidity_fade_source.py',
    'src/trading_runtime/strategy_one_intent.py',
    'src/trading_runtime/strategy_one_management_snapshot.py',
    'src/trading_runtime/strategy_profit_giveback_arm.py',
    'src/trading_runtime/strategy_profit_giveback_exit.py',
    'src/trading_runtime/strategy_profit_giveback_source.py',
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/strategy_rising_momentum_witness.py',
    'src/trading_runtime/strategy_sixty_one_contract.py',
    'src/trading_runtime/strategy_sixty_one_release.py',
    'src/trading_runtime/squeeze_ladder_geometry.py',
    'src/trading_runtime/all_held_original_risk_failure.py',
    'src/trading_runtime/confirmed_original_risk_failure.py',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
    'src/trading_runtime/original_risk_diagnostic_profile.py',
    'src/backend/backtest_confirmed_original_risk_source.py',
    'src/backend/backtest_market_data.py',
    'src/backend/backtest_declared_ladder_plan.py',
    'src/backend/backtest_ladder_source_authority.py',
    'src/trading_runtime/squeeze_ladder_automatic.py',
    'src/trading_runtime/original_risk_checkpoint.py',
    'src/trading_runtime/original_risk_pending_snapshot.py',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py',
)

# Reviewed complete native execution, independent cold proof and release routes.
STRATEGY61_SOURCE_AST = {'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/trading_runtime/strategy_sixty_one_contract.py': 'bad0a3e37a044e7885ff34a886520901fa27bb8107688c005511bf040e8abf0c', 'src/backend/backtest_strategy_initial_momentum.py': '954446cab169240a183801637b4f61b27f90d45467b065ef428760d1ed5a48df', 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856', 'src/trading_runtime/strategy_profit_giveback_arm.py': '5757092f1089572ad5ae39a6c482932fc8629930413e48b5c6944dc742d2358c', 'src/trading_runtime/strategy_liquidity_fade_exit.py': '3ccbc3b5f09d6553a23372e9a2c435c936defa2dc800717d36f2824c21a04325', 'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8', 'src/trading_runtime/strategy_profit_giveback_exit.py': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e', 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5fce50ffc270e19c98f200dbe949edd9ce718959d8f7cb48324dce5f1e96590f', 'src/trading_runtime/strategy_liquidity_fade_source.py': '0a92702b17b61fba3112bf040afa8bc764ca3df2656ef25bd90130b13714b189', 'src/trading_runtime/arte_journal_commit_v4.py': 'fd634b0f4f16f673f6795479fd03ef258c81b33b6e264f5395305b936c4b0a1f', 'src/trading_runtime/strategy_half_risk_liquidity_fade.py': '3f8d8949125090eda81d3256cae65920f950da12cbbb88dc6fb30a922c4cbe21', 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38', 'src/trading_runtime/strategy_fifty_release.py': '2f078566963e645c725d14526621e87a99d623f415fa010aa2440921e8d87f1b', 'src/trading_runtime/strategy_one_intent.py': '55258f2be65ba5485276a54e3384d8bd2cdb32dc39a66520e0f0cb98bc2ae97b', 'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d', 'src/backend/backtest_v4_saved_review.py': 'f6a2048ea195b7a39e6694e89cb7f164e4d83826197727ee90dceefb5e852ce9', 'src/trading_runtime/strategy_sixty_one_release.py': '479ee89934bbe52abd9b63680646d6c84d225a85b034c237194bb2ff84851f05', 'src/trading_runtime/strategy_rising_momentum_witness.py': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30', 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea', 'pipelines/strategy_one/configuration_publisher.py': 'fe6f71767e450e7a6c8c7f59dd2fbdf309b095f6a7de9e3342f25292176b6fc6', 'src/trading_runtime/arte_profit_giveback_v4.py': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293', 'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0', 'scripts/clickhouse/publish_strategy_sixty_one_configuration.py': 'bd3c5852f8f4e450e386683e56abfca4cc9c970a35bf51a4939717a9cc5136a1', 'src/backend/backtest_strategy_one_configuration.py': '6a7720a1ce6223e3fff3364d95a3524442c8d37ad2dea0bff121b5eaab750f29', 'src/backend/backtest_strategy_initial_price_break.py': 'aa5968f100749c56e97c66279ba4bd1feb583e9b3a8f8a978718f234b2d98ff6', 'src/trading_runtime/arte_oms_projection.py': 'e0accf89d43ab445f0d0520d4b4b811c86381b540043fc1be9ee982dd419e25f', 'src/backend/backtest_strategy_one_stateful.py': 'fec2d6e1dbe5913f1e3d8b856e58f73e018687e642ffa906dd647a534f46703b', 'src/trading_runtime/strategy_profit_giveback_source.py': '1ed0dcde9e4a462519e773069686a1a95731534312d6a5a290fa351290aefa29', 'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e', 'src/trading_runtime/arte_followthrough_failure_v4.py': '2ce1e6805a9db71fa5afe0ec63aba61a7d87fc95e3847b89bd7e94b93aa1bc6c', 'scripts/clickhouse/publish_strategy_fifty_configuration.py': '070b8ce29c010b6ebf9448510b916ed97a186838e8b9ce048ae6694e43b4ac07', 'src/trading_runtime/strategy_registry.py': '570c8d53eb1133a5b14f403b71d8804970f16c65aede3d12a8ae05ee579fbc13', 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': '5f8b90176e9bbe6232a1f7b88a86333cc2466dc5a932740d27b7cbf4d4345435', 'src/backend/backtest_fixed_v4_certification.py': 'b8852737ad215dd18c7a9f3587540ec4592d039e78580977c6f94a7a24537d58', 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d', 'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334', 'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33', 'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1', 'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d', 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92', 'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c', 'src/backend/backtest_strategy_one_management.py': '7ac9b315872470ac627a6a623cf683d2260cd07ccc5a516ae6698c2b60d89833', 'src/trading_runtime/arte_first_price_entry_v4.py': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6', 'src/trading_runtime/strategy_fifty_contract.py': 'de010651cca50ec4995d75c26a03c2818cae65ca7a8b998ef741a91bcaa9a1a2', 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '216e413631d81e7cb0b29d7103ab1939320946a43a6efeba17c646cdc6147134', 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': '5b5fbaeda54d8e7593d6646c7d6631ab8323ee472cdf73ecee947e049a77b50a', 'src/backend/backtest_strategy_liquidity_fade.py': '4870509aaef5af9a6b45d7a9c389dfe737a927f0beb71164b00ef9919d1a7e24', 'src/trading_runtime/strategy_followthrough_exit.py': '85057326d73128bfe04f23fdd72f433693aa49b5837a06354bdba1e5a76b628b', 'src/trading_runtime/declared_followthrough_failure.py': 'ad60271c51250ee004e00e5fc00f8bd8e220794968a9421ec0ff73bd002d5c98', 'src/trading_runtime/strategy_liquidity_fade_publication.py': '3ae4e11e8b2d2d9f470c4beab7c0af09b0259c1168a367f728778ed8accb6827', 'src/backend/backtest_strategy_first_price_source.py': 'a2557a2973a3ad1371fd9d97ffe740cf0687aa510372b1d6471d0284f92d2146', 'scripts/clickhouse/report_strategy_one_trades.py': 'ae5e51880141272adf310070c163d59c287ee90433e87ec89490414f0f581f3f', 'src/backend/backtest_typed_publisher.py': '0b496a1667ee617a96238773ec2cbc306b4a4f798db2fc4ea0280f7d7767661f', 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'd6e5660d40bb75df739f8fe862ac7a8ed8441cdd7c58da64184f219d5d8a62b4', 'pipelines/strategy_one/strategy_sixty_one_configuration.py': '0510edbad136cc24d937c110764151c0a51f999541f3a0f904722df0839282c1', 'src/trading_runtime/strategy_one_management_snapshot.py': '102cb6778c4cde6bd89393cd7338962b0092a9ad9414b57b17976081299f90b3', 'pipelines/strategy_one/strategy_fifty_configuration.py': 'bde8696f70d4dfd16c12781832fb6e8b566482c4f7c5e2460c62cb3a9634bff9', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
    'src/trading_runtime/all_held_original_risk_failure.py': '093bb7f1b73277f21aa1a399a7b3b9f36d77dea8ca614f1130314029fcfaf8cf',
    'src/trading_runtime/confirmed_original_risk_failure.py': '653f8f54f8800c9b9c8dac7804fd6ef2d1ae83a2a8785aef258f0787c1413197',
    'src/trading_runtime/arte_original_risk_diagnostic_v4.py': 'c048997055bb65060ca280d197b986994a4a4c1fee2eca883ec2c5cc9fbe5a51',
    'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
    'src/backend/backtest_confirmed_original_risk_source.py': '0df8a2bda59e1ce95b150ca9ff43a22a2752f5a602856eaeee0cf6d866dac6f0',
    'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
    'src/backend/backtest_declared_ladder_plan.py': {'automatic_policy': 'f5383587a2b55fcd05c6c6f0872d7e641b1059d6ee229094f5a10ef15b5c2669'},
    'src/backend/backtest_ladder_source_authority.py': {'declared_ladder_policy': '56c0bef2dbea0bccd752d45293520c1ce8ebec0b3e2829c48c5a2a7eccbe360c'},
    'src/trading_runtime/squeeze_ladder_automatic.py': {'AutomaticLadderPolicy': '8e6b530d31208cc0c139f51eee83da902e67a16f532118b6f7eacc90a30f936f'},

    'src/trading_runtime/original_risk_checkpoint.py': '9049359bdf28ddbbbfdebeaa31cf48dfe4fd035cae561869f0a1e831ed4a1cee',
    'src/trading_runtime/original_risk_pending_snapshot.py': 'bef2be65cd8fa6d02a435bc8290b3e765bb496567e3abb7b3d7fd912311239a7',
    'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'a6011032b9690f0ddb72e8db54401e57e7850431e243023f3c3b8d90d524643c',
    'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
}


def certify_strategy_sixty_one_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if set(STRATEGY61_SOURCE_AST) != set(REQUIRED_SOURCE_FILES):
        raise ValueError('Strategy61 complete source review is not sealed')
    if set(overrides) - set(STRATEGY61_SOURCE_AST):
        raise ValueError('Strategy61 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY61_SOURCE_AST.items():
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
            raise ValueError('Strategy61 source cannot be parsed: ' + relative) from exc
        if actual != expected:
            raise ValueError('Strategy61 pinned source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
