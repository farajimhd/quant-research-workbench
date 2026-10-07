"""Exact selected prefix compatibility; legacy reviewed pin remains normative."""
import ast
from pathlib import Path
import pytest
from src.backend.backtest_fixed_v4_certification import (
    _reviewed_runtime_entry_wrapper, _RISING_MOMENTUM_REVIEWED_AST,
)
ROOT = Path(__file__).resolve().parents[1]
PIN = _RISING_MOMENTUM_REVIEWED_AST["trading_runtime/runtime.py"]["_strategy_one_entry_intent"]

def wrapper():
    tree = ast.parse((ROOT / "src/trading_runtime/runtime.py").read_text(encoding='utf-8'))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and n.name == "_strategy_one_entry_intent")

def test_reviewed_selected_prefix_and_exact_old_factory():
    current = wrapper()
    assert _reviewed_runtime_entry_wrapper(current, PIN)
    legacy = ast.parse(ast.unparse(current)).body[0]
    del legacy.body[1:3]
    assert _reviewed_runtime_entry_wrapper(legacy, PIN)
    assert PIN == "c193305101040e954be086e2f46ccf560cd7c373fe3819157da9d35c045e5c63"

@pytest.mark.parametrize("old,new", [
    ("type(selected) is not NativeFixedStructuralLotOperation", "selected is None"),
    ("selected.source.require_installed_admission()", "pass"),
    ("selected.source.run_id != self.run_id", "False"),
    ("selected.source._revision != self.config.strategy_revision", "False"),
    ("selected.source._strategy_id != self.config.strategy_id", "False"),
    ("return selected.request(proposal).intent", "return proposal"),
    ("getattr(self, '_fixed_structural_lot_operation', None)", "getattr(self, '_other', None)"),
])
def test_selected_prefix_mutation_rejected(old, new):
    source = ast.unparse(wrapper())
    assert old in source
    changed = ast.parse(source.replace(old, new, 1)).body[0]
    assert not _reviewed_runtime_entry_wrapper(changed, PIN)

def test_old_factory_mutation_and_extra_statement_rejected():
    current = wrapper()
    current.body.append(ast.parse("return proposal").body[0])
    assert not _reviewed_runtime_entry_wrapper(current, PIN)
    current = wrapper()
    current.body.insert(3, ast.parse("pass").body[0])
    assert not _reviewed_runtime_entry_wrapper(current, PIN)

from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_journal_projection

def test_exact_selected_journal_class_restores_old_reviewed_class_and_fence():
    source = (ROOT / "src/backend/backtest_journal_memory.py").read_text(encoding='utf-8')
    pins = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_journal_memory.py"]
    for name in ("BacktestMemoryJournal", "mark_fenced"):
        assert _reviewed_fixed_lot_journal_projection(source, name, pins[name])
    assert not _reviewed_fixed_lot_journal_projection(source, "append_many", "foreign")
    assert not _reviewed_fixed_lot_journal_projection(source, "BacktestMemoryJournal", "foreign")

@pytest.mark.parametrize("old,new", [
    ("request.verify()", "pass"),
    ("context.owner.verify_recovery_record(context,record)", "pass"),
    ("request.owner._verify_issued_request(request)", "pass"),
    ("self._fixed_structural_lot_entries.pop(record.record_id, None)", "pass"),
    ("self._initial_lot_profiles.clear()", "pass"),
    ("self._next_sequence += len(result)", "self._next_sequence += 1"),
])
def test_journal_selected_and_legacy_mutations_rejected(old, new):
    source = (ROOT / "src/backend/backtest_journal_memory.py").read_text(encoding='utf-8')
    assert old in source
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_journal_memory.py"]["BacktestMemoryJournal"]
    assert not _reviewed_fixed_lot_journal_projection(source.replace(old, new, 1), "BacktestMemoryJournal", pin)

from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_configuration_projection

def test_exact_selected_configuration_restores_whole_old_module():
    source = (ROOT / "src/backend/backtest_strategy_one_configuration.py").read_text(encoding='utf-8')
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_configuration.py"]["__module__"]
    assert _reviewed_fixed_lot_configuration_projection(source, "__module__", pin)
    assert not _reviewed_fixed_lot_configuration_projection(source, "__module__", "foreign")

@pytest.mark.parametrize("old,new", [
    ("verify_fixed_structural_lot_configuration(strategy)", "pass"),
    ("declared_fixed_structural_lot_contract(strategy_number) is None", "False"),
    ("number=strategy_number", "number=42"),
    ("source_number = numbered_strategy_parent(strategy_number)", "source_number = 1"),
    ("strategy.get(\"execution_interval\") != \"100ms\"", "False"),
])
def test_configuration_selected_and_old_authority_mutations_rejected(old, new):
    source = (ROOT / "src/backend/backtest_strategy_one_configuration.py").read_text(encoding='utf-8')
    assert old in source
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_configuration.py"]["__module__"]
    assert not _reviewed_fixed_lot_configuration_projection(source.replace(old, new, 1), "__module__", pin)

from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_execution_projection

def test_execution_reinlines_exact_legacy_factory():
    source = (ROOT / "src/backend/backtest_strategy_one_execution.py").read_text(encoding='utf-8')
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_execution.py"]["run_certified_strategy_one_session"]
    assert _reviewed_fixed_lot_execution_projection(source, "run_certified_strategy_one_session", pin)
    assert not _reviewed_fixed_lot_execution_projection(source, "run_certified_strategy_one_session", "foreign")

@pytest.mark.parametrize("old,new", [
    ("selected_session.require(market=market", "selected_session.other(market=market"),
    ("strategy_number=12", "strategy_number=1"),
    ("candidate_indices=base_gate.eligible_indices", "candidate_indices=()"),
    ("after_boundary_ms=(0 if selected_session", "after_boundary_ms=(100 if selected_session"),
    ("Strategy 35 resume lacks liquidity-cache equivalence acceptance", "Changed guard"),
])
def test_execution_selected_and_extracted_old_pipeline_mutations_rejected(old, new):
    source = (ROOT / "src/backend/backtest_strategy_one_execution.py").read_text(encoding='utf-8')
    assert old in source
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_execution.py"]["run_certified_strategy_one_session"]
    assert not _reviewed_fixed_lot_execution_projection(source.replace(old, new, 1), "run_certified_strategy_one_session", pin)

from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_management_projection

def test_complete_default_manager_restoration_for_all_selected_old_pins():
    source = (ROOT / "src/backend/backtest_strategy_one_management.py").read_text(encoding='utf-8')
    for name, pin in _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_management.py"].items():
        assert _reviewed_fixed_lot_management_projection(source, name, pin)

@pytest.mark.parametrize("old,new", [
    ("owner.operation.source.require_installed_admission()", "pass"),
    ("confirmed.target!=source.initial_target", "False"),
    ("self._fixed_lot_owner.register_entry(request,results[0]['order_group']['group_id'])", "pass"),
    ("self._positions[key]=confirmed", "pass"),
    ("await self._fixed_lot_owner.retire(key)", "pass"),
    ("tick <= 0", "tick < 0"),
])
def test_manager_selected_and_old_state_guard_mutations_rejected(old, new):
    source = (ROOT / "src/backend/backtest_strategy_one_management.py").read_text(encoding='utf-8')
    assert old in source
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_strategy_one_management.py"]["__init__"]
    assert not _reviewed_fixed_lot_management_projection(source.replace(old, new, 1), "__init__", pin)

from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_typed_projection

def test_selected_typed_projector_restores_exact_old_function():
    source = (ROOT / "src/backend/backtest_typed_projection.py").read_text(encoding='utf-8')
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_typed_projection.py"]["project_pending_backtest_v4_prefix"]
    assert _reviewed_fixed_lot_typed_projection(source, "project_pending_backtest_v4_prefix", pin)

@pytest.mark.parametrize("old,new", [
    ("request is None or expected_config.get('mode') != 'backtest'", "False"),
    ("expected_config['strategy_revision'] != request.revision", "False"),
    ("journal.fixed_lot_recovery_record(record.record_id)", "None"),
    ("source_sequence=record.sequence", "source_sequence=0"),
    ("fixed_lot_units[source[1].intent_id]", "None"),
])
def test_projector_selected_source_mutation_rejected(old,new):
    source = (ROOT / "src/backend/backtest_typed_projection.py").read_text(encoding='utf-8')
    assert old in source
    pin = _RISING_MOMENTUM_REVIEWED_AST["backend/backtest_typed_projection.py"]["project_pending_backtest_v4_prefix"]
    assert not _reviewed_fixed_lot_typed_projection(source.replace(old,new,1), "project_pending_backtest_v4_prefix", pin)

from copy import deepcopy
from src.backend import backtest_fixed_v4_certification as certifier

@pytest.mark.parametrize("key", tuple(certifier._FIXED_LOT_LEGACY_AST_RECIPES))
def test_every_closed_recipe_restores_retained_legacy_pin(key):
    relative, name = key
    path = ROOT / relative if (ROOT / relative).is_file() else ROOT / "src" / relative
    source = path.read_text(encoding='utf-8')
    recipe = certifier._FIXED_LOT_LEGACY_AST_RECIPES[key]
    assert certifier._reviewed_fixed_lot_ast_recipe(source, relative, name, recipe["legacy_ast"])
    tree = ast.parse(source)
    node = tree if name == "__module__" else next(n for n in ast.walk(tree)
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name == name)
    node.body.append(ast.Pass())
    assert not certifier._reviewed_fixed_lot_ast_recipe(ast.unparse(tree),relative,name,recipe["legacy_ast"])

@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_fragment", "wrong_location", "foreign_pin"])
def test_closed_recipe_rejects_missing_duplicate_foreign_edits(monkeypatch,change):
    key = next(iter(certifier._FIXED_LOT_LEGACY_AST_RECIPES))
    recipes = deepcopy(certifier._FIXED_LOT_LEGACY_AST_RECIPES)
    recipe = recipes[key]
    if change == "missing": recipe["edits"].pop()
    elif change == "duplicate": recipe["edits"].append(deepcopy(recipe["edits"][0]))
    elif change == "wrong_fragment": recipe["edits"][0]["current_hash"] = "0" * 64
    elif change == "wrong_location": recipe["edits"][0]["path"] = ["foreign"]
    elif change == "foreign_pin": recipe["legacy_ast"] = "0" * 64
    monkeypatch.setattr(certifier,"_FIXED_LOT_LEGACY_AST_RECIPES",recipes)
    relative,name = key
    pin = _RISING_MOMENTUM_REVIEWED_AST[relative][name]
    assert not certifier._reviewed_fixed_lot_ast_recipe((ROOT/"src"/relative).read_text(encoding='utf-8'),relative,name,pin)

@pytest.mark.parametrize("change", ["bool_step", "bool_count", "bool_start", "negative_count", "unknown_key", "unknown_op"])
def test_recipe_closed_typed_path_and_cardinality(monkeypatch,change):
    key = next(iter(certifier._FIXED_LOT_LEGACY_AST_RECIPES))
    recipes = deepcopy(certifier._FIXED_LOT_LEGACY_AST_RECIPES)
    edit = next(e for e in recipes[key]["edits"] if e["op"] == "splice")
    if change == "bool_step": edit["path"] = [True]
    elif change == "bool_count": edit["count"] = True
    elif change == "bool_start": edit["start"] = False
    elif change == "negative_count": edit["count"] = -1
    elif change == "unknown_key": edit["foreign"] = None
    elif change == "unknown_op": edit["op"] = "ignore"
    monkeypatch.setattr(certifier,"_FIXED_LOT_LEGACY_AST_RECIPES",recipes)
    relative,name = key
    assert not certifier._reviewed_fixed_lot_ast_recipe((ROOT/"src"/relative).read_text(encoding='utf-8'),relative,name,
        _RISING_MOMENTUM_REVIEWED_AST[relative][name])

def test_core_exact_single_guard_extension_restores_complete_old_function():
    tree = ast.parse(Path(certifier.__file__).read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                    and n.name == "certify_drawdown_measure_core_source")
    changes = 0
    for node in ast.walk(function):
        if isinstance(node,ast.If) and "_reviewed_fixed_lot_core_projection" in ast.unparse(node.test):
            node.test = ast.parse("if len(digests) != 1 or type(digests[0]) is not str or digests[0] != digest:pass").body[0].test
            changes += 1
    from hashlib import sha256
    assert changes == 1
    assert sha256(ast.unparse(function).encode()).hexdigest() == certifier._DRAWDOWN_CORE_LEGACY_SELF_AST

def test_replay_recipe_uses_explicit_utf8_authority_and_rejects_locale_decode():
    relative = "backend/replay_run_service.py"
    raw = (ROOT / "src" / relative).read_bytes()
    source = raw.decode("utf-8")
    wrong = raw.decode("cp1252")
    recipe = certifier._FIXED_LOT_LEGACY_AST_RECIPES[(relative,"__module__")]
    assert recipe["legacy_ast"] == "341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1"
    assert certifier._reviewed_fixed_lot_ast_recipe(source,relative,"__module__",recipe["legacy_ast"])
    assert not certifier._reviewed_fixed_lot_ast_recipe(wrong,relative,"__module__",recipe["legacy_ast"])


def test_profit_certifier_exact_guard_restoration_and_unchanged_ordered_map():
    from hashlib import sha256
    from src.backend import backtest_strategy_profit_certification as profit
    tree=ast.parse(Path(profit.__file__).read_text(encoding='utf-8'))
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign))
    assert sha256(ast.unparse(assignment).encode()).hexdigest()=='8424bc2ad5ee80263c9a13c3ba9d8f3aac151a9676d14e7a1df7e1f88d7b9850'
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef))
    loops=[n for n in ast.walk(function) if isinstance(n,ast.For)
           and ast.dump(n.target,include_attributes=False)==ast.dump(
               ast.parse('for (name,digest),summary in values:pass').body[0].target,include_attributes=False)]
    assert len(loops)==1 and len(loops[0].body)==2
    loops[0].body=ast.parse("if summary.name != name or len(summary.digests) != 1 or summary.digests[0] != digest:\n raise ValueError('Strategy 31 reviewed profit-route authority changed: ' + relative + ':' + name)").body
    assert sha256(ast.unparse(function).encode()).hexdigest()=='10966d3da56264ae2f17152fd7291215239cc98066cabd5eddc37f04c04c5e81'
    assert sha256(ast.unparse(tree).encode()).hexdigest()=='6ffe7c60903fc6d862053e0ecad6701cee2803f2d19db049a55621253a1b57ff'


def test_profit_module_execution_requires_independent_extracted_helper(monkeypatch):
    relative='src/backend/backtest_strategy_one_execution.py'
    source=(ROOT/relative).read_text(encoding='utf-8')
    from src.backend.backtest_strategy_profit_certification import REVIEWED_PROFIT_ROUTE
    pin=REVIEWED_PROFIT_ROUTE[relative]['__module__']
    assert certifier._reviewed_fixed_lot_profit_projection(source,relative,'__module__',pin)
    monkeypatch.setattr(certifier,'_reviewed_fixed_lot_execution_projection',lambda *args:False)
    assert not certifier._reviewed_fixed_lot_profit_projection(source,relative,'__module__',pin)


@pytest.mark.parametrize('authority',['profit','liquidity','entry_activity'])
def test_actual_selected_source_certifiers_reject_changed_whole_module(tmp_path,authority):
    path=tmp_path/'changed.py'
    relative='pipelines/strategy_one/configuration_publisher.py'
    path.write_text((ROOT/relative).read_text(encoding='utf-8')+'\npass\n',encoding='utf-8')
    if authority=='profit':
        from src.backend.backtest_strategy_profit_certification import certify_profit_giveback_route_source as call
    elif authority=='liquidity':
        from src.backend.backtest_strategy_liquidity_fade_certification import certify_prepared_liquidity_fade_source as call
    else:
        from src.backend.backtest_strategy_entry_activity_certification import certify_entry_activity_source as call
    with pytest.raises(ValueError,match='source changed|authority changed'):
        call(source_overrides={relative:path})


@pytest.mark.parametrize('module',['liquidity_fade','entry_activity','episode_activity',
    'thirty_eight','thirty_nine','forty','forty_one','forty_two'])
def test_source_certifier_only_guard_delta_restores_whole_old_module_and_map(module):
    import subprocess
    path=f'src/backend/backtest_strategy_{module}_certification.py'
    old_source=subprocess.check_output(['git','show',
        f'a7b47a38632cf83bab8e0fc0c165b71d7b30e35a:{path}'],cwd=ROOT).decode('utf-8')
    source=(ROOT/path).read_text(encoding='utf-8')
    old=ast.parse(old_source);new=ast.parse(source)
    oldmap=next(n for n in old.body if isinstance(n,ast.Assign))
    newmap=next(n for n in new.body if isinstance(n,ast.Assign))
    assert ast.dump(newmap,include_attributes=False)==ast.dump(oldmap,include_attributes=False)
    assert source.splitlines()[newmap.lineno-1:newmap.end_lineno]==old_source.splitlines()[oldmap.lineno-1:oldmap.end_lineno]
    branches=[n for n in ast.walk(new) if isinstance(n,ast.If)
              and len(n.body)==2 and isinstance(n.body[0],ast.ImportFrom)
              and n.body[0].module=='backtest_fixed_v4_certification']
    assert len(branches)==1
    branch=branches[0]
    assert len(branch.body)==2 and isinstance(branch.body[0],ast.ImportFrom)
    assert isinstance(branch.body[1],ast.If) and len(branch.body[1].body)==1
    assert '_reviewed_fixed_lot_profit_projection' in ast.unparse(branch.body[1].test)
    branch.body=branch.body[1].body
    assert ast.dump(new,include_attributes=False)==ast.dump(old,include_attributes=False)
