"""Source seal for Strategy36 entry activity, publication and execution routes.

The complete release certificate composes this with the full Strategy35 proof.
This source seal alone establishes neither market coverage nor profitability.
"""
import ast
from src.backend.source_ast_summary import canonical_module_ast_digest
from hashlib import sha256
import json
from pathlib import Path


ENTRY_ACTIVITY_SOURCE_AST = {'scripts/clickhouse/publish_strategy_thirty_six_configuration.py': '9181d082d60d5269e13b69448febc5100b955ff42a9a9acbce0735f28bfd71a9',
 'src/trading_runtime/strategy_entry_activity_fade.py': '78219596eedb2c3de41ed0597f4e5bde5efea9d68854645f2786ad53fa5c4b75',
 'src/trading_runtime/strategy_entry_activity_witness.py': 'ce4bfd96579271bd7d7124e700c40824e9c870266ff05d9ea10b50bcb4d72bc8',
 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92',
 'src/backend/backtest_strategy_episode_activity_source.py': '2d4fa40c46a866be83328ce7443a350655620bf9c4b4a82044dc45ee7441c8c0',
 'src/backend/backtest_strategy_episode_activity_gate.py': '43eaa3e95701f32ab02f5af501ca0e019b03ed91badb8f1bcb62ced3918510b5',
 'src/trading_runtime/strategy_episode_activity_veto.py': 'cb96806e4594bdb2b087b1c12925ee80d296e1313984dce6329f3eee4b911e06',
 'src/backend/backtest_strategy_entry_activity_gate.py': 'a128c7856cbc77854871961ec4d582b5f44b9bdeee517f6dde61360811fd02d4',
 'src/backend/backtest_strategy_entry_activity_source.py': 'db6e9f16dd62c48f98dc5ec8f7bfa40a17f69974bb618097418ec3f246d91681',
 'src/trading_runtime/strategy_thirty_six_release.py': '9a243bfe59b4071497bac22231ac0fb104262af40ff282e4910e6819a8b29308',
 'pipelines/strategy_one/strategy_thirty_six_configuration.py': '4a03259d1ab4a0ae29a2a14672fdef94249cac0b614478817b9f22d8a91d34b6',
 'pipelines/strategy_one/configuration_publisher.py': '83a24b2396e1d01ac7f7fa8f225f86bfc39772c0ba86a6f533347ee51a64e7b7',
 'src/backend/backtest_strategy_one_configuration.py': 'dc1b27487c5d05a1484bd4726bf63503deb3b9ec9f123e0e763fb2b3c4b94fac',
 'src/trading_runtime/strategy_registry.py': '507e8b1559e23586293c7cadb8ab3d72732ec027e38f280209842bdd12f62c19',
 'src/trading_runtime/numbered_fixed_strategy.py': 'a6a6147f4d001a6217512a74cc35fabd51918984eacd43e7800b4ba2ba94dd0b',
 'src/backend/backtest_strategy_certified_price_break.py': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38',
 'src/backend/backtest_strategy_one_execution.py': 'd9ed2de2119170cf4650b139e16004c170fd2503a482831b85f40a1177ab6f8d',
 'src/backend/backtest_strategy_one_coordinator.py': '7d40c9a3d75872e1ff263729eb578cf9860e913bae3863ec8889ca999e5df334',
 'src/backend/backtest_strategy_one_management.py': '6800d3df81562539ae5dea1ed12474f298f7a3dcf3cc16985567530601d342f5',
 'src/trading_runtime/strategy_one_management_snapshot.py': '3f239ee12ca02793e6bf2cce4c5a809605593e0f059aa352b32fb4ae5314fa18',
 'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8',
 'src/backend/backtest_journal_memory.py': '7967a2dc2bd11edd2739caa04a8c27c8fb80f3d900da8bb642a5509deaf08f33',
 'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e',
 'src/backend/backtest_typed_publisher.py': '89ebb43227635813d78c07f3751cca2f93cd095c26cc1f9e5b1f809e3ca2da61',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '45dc2b6abf7f528fbf1e056c5249a78eb1b52813f051a732512712abc5993856',
 'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '5d08bb4fca7c8af5ae6ef711f16f99fe38cca561c46207142bb06640cb810d93',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/arte_journal_commit_v4.py': 'dc4c394bf21576d728c39086e97f9c03c41592553d55073a058bb030de353092',
 'src/trading_runtime/arte_journal_compound_v4.py': '05b28c131df86e9b743117c1c801eec7384995b0bbe8c8b80c9afb53e183eb4c',
 'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1',
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
 'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '1ff8cda4665f3fefd70fef5a56ccfdc7cf77a55caf819639f17e38aeffe36739',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': '1c4571237c9231fbe1660349be684adaf52a2b34d19b4c49c9489af9392a1a7c'}


def certify_entry_activity_source(*, source_overrides=None):
    """Fail closed on changes to any reviewed additional authority."""
    overrides = source_overrides or {}
    if set(overrides) - set(ENTRY_ACTIVITY_SOURCE_AST):
        raise ValueError('Entry activity source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in ENTRY_ACTIVITY_SOURCE_AST.items():
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
            raise ValueError('Entry activity source cannot be parsed: ' + relative) from exc
        if actual != expected:
            raise ValueError('Entry activity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
