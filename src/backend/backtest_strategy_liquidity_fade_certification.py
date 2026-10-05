"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {'src/trading_runtime/strategy_half_risk_liquidity_fade.py': 'bb11736528440cc5d4015e67108cfcea86b39d07d49500cfca8113612db179e9',
 'src/backend/backtest_strategy_half_risk_liquidity_fade.py': '266015bf39f14014f1705a8729499b472b8803b5ad5d7619b9f69677f5ed0718',
 'scripts/clickhouse/publish_strategy_thirty_five_configuration.py': '4ea3528baf3efb8b60699af4bb9ee81b5c693c01360912dc86d7a2691fba547b',
 'pipelines/strategy_one/configuration_publisher.py': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c',
 'src/backend/backtest_strategy_one_configuration.py': 'b01f2373be42bfad44440e742b8c5476f12512afa1a7834a089f7175238799e9',
 'src/backend/backtest_strategy_one_coordinator.py': '5c2088264162096bbaa2ccef35253a1916cf20c676ed92052664fa253fabebc8',
 'src/backend/backtest_v4_saved_review.py': 'b99924ed509c964d6ded3fe4194edaddc42768dcf3e3406a65d25ebe9e798d8f',
 'src/trading_runtime/numbered_fixed_strategy.py': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773',
 'src/trading_runtime/strategy_registry.py': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60',
 'src/trading_runtime/strategy_confirmed_ah_failure_source.py': 'ee8d344e486a6252da939dedb656a852a7c24a3324054f02506d49f78df64e10',
 'src/trading_runtime/arte_confirmed_ah_failure_v4.py': 'c17862a35ad567c110523d0e770a668fc0751b36ad3aedc51ccbe239a508294c',
 'src/trading_runtime/strategy_confirmed_ah_failure_exit.py': '5f9b567fe7f3979aab453850eb7d0f884c2f58a6d9e4111563e52c1d54859b13',
 'src/trading_runtime/arte_profit_giveback_v4.py': '2e49a2ce2cc531ffa0581c35988d0df2dfd975707554ef57406aa571fe2e08e2',
 'src/trading_runtime/strategy_profit_giveback_source.py': '985aa74ad51a20de1ccfcfa64bf5242e6df5a5aaec8b8404667dcfebdf6c6864',
 'src/trading_runtime/strategy_profit_giveback_arm.py': 'bd4b0b19dc4fce019ff2f82ae0f96a2b5e117484e933d2314a8a8be8f0740882',
 'src/trading_runtime/strategy_profit_giveback_exit.py': '3bba33536111b224799b99a70aca545aed2f4ec7566279e092a65abe9aee9e22',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '991107a7d1f30074fabe4a58fe4b0812b6af42d855bcbb2eb617ff9a765f57b5',
 'src/trading_runtime/strategy_followthrough_exit.py': '2467b54df4bacefead61db45065dc90aba6879559f8f1373fbc742834daf34c2',
 'src/trading_runtime/arte_strategy_one_entry_journal.py': '26acb77c922fe44e274a3f2ff019c2d81b4f1e2a1846ecca666214e8ecf64222',
 'src/trading_runtime/strategy_rising_momentum_witness.py': 'aec6af5555b8d4eeb3373d3b4162b8c8cab3bbc16660f600da92dcda9384c385',
 'src/trading_runtime/arte_rising_momentum_entry_v4.py': '797985fd7046593e795e0ceb47a5de3edfb637b3ca5eb4cf874151011c6d95c5',
 'src/trading_runtime/arte_initial_momentum_entry_v4.py': '53224434ddf6ca059cbbf2114c06291c76946cf0ddfc005993d3107c7a6114fa',
 'src/trading_runtime/arte_first_price_entry_v4.py': '3a2432ea4c7d82efdfb542cbd18f23b6a35439b62211a08e6226c2b70c879269',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint_reference.py': '2bf2d32ba8e66f95d6646c3f364937dd342fe25af9cd3311632e50bf958369d8',
 'src/backend/replay_run_service.py': 'e868d97892bcdfae8b1c3ef8c5066f4f311458e366a82c30582aa7d976bd6447',
 'src/backend/backtest_strategy_one_execution.py': '850e6ab6a644eceff98c66f988485845944d615bfbf1918fc54d6665797f61a9',
 'src/backend/backtest_strategy_liquidity_fade.py': '41a370888116041396bb10c8a848ef863466134a37457cfd335d820ccdd00fba',
 'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
 'src/trading_runtime/strategy_liquidity_fade_exit.py': '65386d80f023002484218be02dfb3bd7e4715e9170219a31e452e874825e8a25',
 'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '17c70f130523b640e04d4f5397e9f4991dfe5122e9e5afbf508cdc74f6cc37b9',
 'src/trading_runtime/strategy_liquidity_fade_source.py': 'b729d22d831cac041d9ab38f25d213c5058a4e8140a1f3ce71279c8cc370faa2',
 'src/trading_runtime/strategy_liquidity_fade_market_source.py': 'fde06254872bf85500bafa0564d68753c6a908665539b7f3cbb3a7a296083127',
 'src/trading_runtime/strategy_thirty_five_release.py': 'f0e45aba520f8792f65b375929d39063107a209b9b458c559be043d2fe64cfcd',
 'pipelines/strategy_one/strategy_thirty_five_configuration.py': '21bfe1c3c954a7535bf49c3b6a1a0b94cab0f1693c8f8bd0e3e8511856184abe',
 'src/trading_runtime/strategy_liquidity_fade_transport.py': 'd3801bf25d21f372be32000aaef5b5f9a86ba168c37a40a5e9afe0f836184dfe',
 'src/backend/backtest_strategy_liquidity_fade_loader.py': 'b144a37a12b876fa987ded8c421b81690596a60d2b2d59e4edef849e9ba6d5e6',
 'src/trading_runtime/strategy_liquidity_fade_entry_source.py': 'b411bf1a156f61414df8b765eb3f3606d98f79c9c541d781ef86329949238ef0',
 'src/backend/backtest_strategy_liquidity_fade_decision.py': '7aaf49f043a2c30da30ed3a24303e27e80082c7d5b0af510aac06ee1ed68a1b0',
 'src/backend/backtest_strategy_one_financial.py': 'f184a0f434aabb3895b1323d2a22056acb30b4ab9bf301cb8356b1eea2591f0d',
 'src/trading_runtime/arte_liquidity_fade_reader_v4.py': 'de0080bdf30bae6cb32105ee52d369f6769cf472888ccacdf6bba974b4648acf',
 'src/trading_runtime/arte_journal_writer.py': 'e9a61cb75de23211d3059ea39e34a276e8736782f77a84f368c94d12208b6e0b',
 'src/trading_runtime/arte_journal_rowbinary.py': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49',
 'src/trading_runtime/arte_typed_insert_dispatch.py': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5',
 'research/mlops/clickhouse.py': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8',
 'src/trading_runtime/strategy_liquidity_fade_checkpoint.py': '3868ef8025c20487a29e67b5ff44f424cc085a87ca4c7ed8898f2be3caeace53',
 'src/backend/backtest_strategy_one_management.py': '81b15b9bb8f3feae00e4439f8edbbde89cfc3e9a445695bca70da1903010a05c',
 'src/trading_runtime/strategy_one_management_snapshot.py': 'a0e8acb006050ccde6ca19b6f9543e58d3fa0f080195e9b618bb4faacdd6df18',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': 'e6c95a8baff01d352e4a9bf02797faf7408df452aa2eee258836bb8bfa184fbb',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262',
 'src/trading_runtime/arte_oms_projection.py': '797fb4d6a314663d5c455eb8c1d0a712f6b94543b237a8d34da872b9076c37f6',
 'src/trading_runtime/arte_journal_projection.py': '62b7209c08d972952fa01d7099829471371071a33d3f25806dedce983ecda8ba',
 'src/trading_runtime/arte_intent_projection.py': 'b53b0251cd632d6a314632b59c0c1523fa035cc4e19058a4398287d9020ff082',
 'src/trading_runtime/strategy_liquidity_fade_publication.py': '631267f209eb6983cf656a64e2f3ad0df71cfea291bfb215066cfcfcd1f5c528',
 'src/trading_runtime/arte_journal_commit_v4.py': 'a94e1d8d06b377aa60106036ba42889e43c3631cea1698b562545576c361fbb8',
 'src/trading_runtime/arte_journal_compound_v4.py': 'c270c28e1fa80d4ff0da67d1b2cd64f8c6b402c7db812caa2fb2d8399010d9e7',
 'src/backend/backtest_strategy_certified_price_break.py': '4c5a153c90b99f0d50bb554afcfb3f74b33ee753c0779948ec5f0afac4d96a67',
 'src/backend/backtest_journal_memory.py': '6dc2a02199967979d5b93dc8b2af3ed3d9f2c7c6298dd12dd1c1decf5672f48c',
 'src/backend/backtest_typed_projection.py': '05081074d0168f9c6952df453f6c054079a220977a96eb70338e5543a7533d0a',
 'src/backend/backtest_typed_publisher.py': '316ccc51b7a9e93848439fb7eda43158282b2e0d6b59fa45e05c4779494e9233',
 'src/trading_runtime/runtime.py': '338a713370881103586864d0e83b51fcbaa9a55def4309b2213a62d9b095a3c8'}


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
