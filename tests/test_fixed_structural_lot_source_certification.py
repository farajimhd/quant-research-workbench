"""Sealed source and negative controls; no fixture issues source authority."""
import ast
import inspect
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_certification as source


def test_registered_production_projection_reaches_actual_own_certificate(monkeypatch):
    """The real selected dispatcher invokes own authority after its parent.

    Parent proof is an explicit isolation seam; no positive source proof is
    fabricated by this isolated parent-call test; actual complete proofs are
    exercised separately without replacements.
    """
    from src.backend import backtest_fixed_v4_certification as full
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    from src.backend.backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract
    contract = declared_fixed_structural_lot_contract(77)
    assert contract is not None
    actual = full.certify_numbered_fixed_v4_projection
    calls = []
    def parent_only(number):
        calls.append(number)
        assert number == numbered_strategy_parent(contract.strategy_number)
        return 'parent-proof-isolation-seam'
    monkeypatch.setattr(full, 'certify_numbered_fixed_v4_projection', parent_only)
    own_proof=source.certify_fixed_structural_lot_source
    def observed_own():
        calls.append('own')
        return own_proof()
    monkeypatch.setattr(source,'certify_fixed_structural_lot_source',observed_own)
    assert len(actual(contract.strategy_number))==64
    assert calls == [42,'own']


def own_read(monkeypatch, transform):
    """Mutate only the external fresh-source read, never the certifier."""
    target = Path(source.__file__).resolve()
    original = Path.read_text
    observed = []

    def read(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.resolve() == target:
            observed.append(path)
            return transform(text)
        return text

    monkeypatch.setattr(Path, 'read_text', read)
    return observed


def test_unapproved_seal_cannot_issue_capability(monkeypatch):
    def blank(text):
        tree=ast.parse(text);lines=text.splitlines(keepends=True)
        for node in reversed(tree.body):
            if isinstance(node,ast.Assign) and node.targets[0].id in {
                    'REVIEWED_SOURCE_AST','APPROVED_METADATA_ANCHOR','APPROVED_SELF_AST'}:
                name=node.targets[0].id
                lines[node.lineno-1:node.end_lineno]=[name+' = '+('{}' if name=='REVIEWED_SOURCE_AST' else "''")+'\n']
        return ''.join(lines)
    own_read(monkeypatch,blank)
    monkeypatch.setattr(source,'REVIEWED_SOURCE_AST',{})
    monkeypatch.setattr(source,'APPROVED_METADATA_ANCHOR','')
    monkeypatch.setattr(source,'APPROVED_SELF_AST','')
    with pytest.raises(ValueError, match='unapproved; admission remains closed'):
        source.certify_fixed_structural_lot_source()


@pytest.mark.parametrize('option', ['expected_map', 'source_overrides', 'root',
                                    'number', 'policy', 'approved'])
def test_public_boundary_has_no_caller_admission_options(option):
    assert not inspect.signature(source.certify_fixed_structural_lot_source).parameters
    with pytest.raises(TypeError):
        source.certify_fixed_structural_lot_source(**{option: {}})


@pytest.mark.parametrize('extra', ["\nEVIL = True\n", "\nimport os\n",
                                  "\ndef extra():\n    return True\n"])
def test_added_top_level_code_is_rejected_before_unsealed_gate(monkeypatch, extra):
    observed = own_read(monkeypatch, lambda text: text + extra)
    with pytest.raises(ValueError, match='module envelope differs'):
        source.certify_fixed_structural_lot_source()
    assert len(observed) == 1


def test_loaded_caller_pin_map_does_not_override_fresh_source(monkeypatch):
    monkeypatch.setattr(source, 'REVIEWED_SOURCE_AST',
                        dict.fromkeys(source.REQUIRED_SOURCE_FILES, '0' * 64))
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        source.certify_fixed_structural_lot_source()


@pytest.mark.parametrize('name,value', [
    ('APPROVED_METADATA_ANCHOR', '0' * 64),
    ('APPROVED_SELF_AST', '0' * 64),
    ('REQUIRED_SOURCE_FILES', ('src/foreign.py',)),
])
def test_loaded_approval_and_inventory_changes_reject(monkeypatch, name, value):
    monkeypatch.setattr(source, name, value)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        source.certify_fixed_structural_lot_source()


def test_fresh_declaration_reordering_rejects(monkeypatch):
    def change(text):
        old = f"APPROVED_METADATA_ANCHOR = {source.APPROVED_METADATA_ANCHOR!r}\nAPPROVED_SELF_AST = {source.APPROVED_SELF_AST!r}"
        assert text.count(old) == 1
        return text.replace(old, f"APPROVED_SELF_AST = {source.APPROVED_SELF_AST!r}\nAPPROVED_METADATA_ANCHOR = {source.APPROVED_METADATA_ANCHOR!r}")

    observed = own_read(monkeypatch, change)
    with pytest.raises(ValueError, match='module envelope differs'):
        source.certify_fixed_structural_lot_source()
    assert len(observed) == 1


def test_fresh_public_function_options_reject(monkeypatch):
    def change(text):
        old = 'def certify_fixed_structural_lot_source() -> str:'
        assert text.count(old) == 1
        return text.replace(old, 'def certify_fixed_structural_lot_source(expected_map=None) -> str:')

    observed = own_read(monkeypatch, change)
    with pytest.raises(ValueError, match='module envelope differs'):
        source.certify_fixed_structural_lot_source()
    assert len(observed) == 1


def test_proposed_inventory_contains_actual_selected_authorities():
    required = source.REQUIRED_SOURCE_FILES
    assert len(set(required)) == len(required)
    for name in (
        'src/backend/backtest_fixed_structural_lot_native.py',
        'src/backend/backtest_fixed_structural_lot_source.py',
        'src/backend/backtest_fixed_structural_lot_management.py',
        'src/trading_runtime/fixed_structural_lot_release.py',
        'src/trading_runtime/fixed_structural_lot_entry_schema.py',
        'src/trading_runtime/fixed_structural_lot_snapshot.py',
        'src/trading_runtime/arte_journal_writer.py',
        'src/trading_runtime/arte_journal_commit_v4.py',
        'src/trading_runtime/order_management.py',
        'src/trading_runtime/portfolio.py',
        'research/mlops/clickhouse.py',
    ):
        assert name in required
    root = Path(source.__file__).resolve().parents[2]
    assert all((root / name).is_file() for name in required)
    tree = ast.parse(Path(source.__file__).read_text(encoding='utf-8'))
    assert len([n for n in tree.body if type(n) is ast.FunctionDef]) == 1


def test_selected_owners_direct_repo_imports_cannot_be_omitted():
    """This guard does not pretend the unfinished transitive closure is sealed."""
    root = Path(source.__file__).resolve().parents[2]
    required = source.REQUIRED_SOURCE_FILES
    missing = []
    for relative in required:
        if ('fixed_structural_lot' not in relative
                and not relative.endswith('independent_lot_stop_amendment.py')
                and 'strategy_seventy_seven' not in relative):
            continue
        package = relative[:-3].split('/')[:-1]
        for node in ast.walk(ast.parse((root / relative).read_text(encoding='utf-8'))):
            if type(node) is not ast.ImportFrom:
                continue
            base = '.'.join(package[:len(package) - node.level + 1]) if node.level else ''
            module = '.'.join(x for x in (base, node.module) if x)
            dependency = root / (module.replace('.', '/') + '.py')
            if dependency.is_file():
                path = dependency.relative_to(root).as_posix()
                if path not in required:
                    missing.append((relative, node.lineno, path))
    assert missing == []


def test_actual_sealed_own_source_passes_without_overrides():
    assert tuple(source.REVIEWED_SOURCE_AST)==source.REQUIRED_SOURCE_FILES
    assert len(source.certify_fixed_structural_lot_source())==64


def test_changed_selected_source_leaf_rejects_current_seal(monkeypatch):
    target=Path(source.__file__).parents[2]/'src/backend/backtest_fixed_structural_lot_native.py'
    original=Path.read_text
    def changed(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\npass\n' if path.resolve()==target.resolve() else text
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match='reviewed source authority changed'):
        source.certify_fixed_structural_lot_source()


def test_changed_certifier_guard_body_rejects_self_selector(monkeypatch):
    own_read(monkeypatch,lambda text:text.replace(
        "raise ValueError('Fixed-lot source pins are malformed')",
        "raise ValueError('changed guard')"))
    with pytest.raises(ValueError,match='function source differs'):
        source.certify_fixed_structural_lot_source()
