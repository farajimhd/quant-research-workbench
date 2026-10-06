import ast
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib
import os
from pathlib import Path

import pytest

from src.backend.source_ast_summary import SourceAstSummaryCache, SymbolObservation


def digest(node):
    return sha256(ast.unparse(node).encode()).hexdigest()


def test_exact_module_semantics_and_text_key():
    cache = SourceAstSummaryCache()
    source = "# comment\ndef f(x):\n    return x + 1\n"
    assert cache.module_digest(source) == digest(ast.parse(source))
    assert cache.module_digest(source) == digest(ast.parse(source))
    assert cache.info().hits == 1
    changed_comment = source.replace("comment", "changed")
    assert cache.module_digest(changed_comment) == cache.module_digest(source)
    assert cache.info().misses == 2  # Exact text, not normalized AST, is the key.


def test_symbol_scope_duplicate_kind_and_immutable_results():
    source = """class same:
    def nested(self): pass
def same(): pass
async def same(): pass
def outer():
    def nested(): pass
"""
    cache = SourceAstSummaryCache()
    names = ("__module__", "same", "nested", "missing")
    result = cache.symbol_summary(source, names)
    tree = ast.parse(source)
    for observation in result:
        nodes = ([tree] if observation.name == "__module__" else
                 [node for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.name == observation.name])
        assert observation.digests == tuple(digest(node) for node in nodes)
    assert tuple(len(row.digests) for row in result) == (1, 3, 2, 0)
    functions = cache.symbol_summary(source, ("same",), kinds=("FunctionDef", "AsyncFunctionDef"))
    assert len(functions[0].digests) == 2
    with pytest.raises(AttributeError):
        result[0].digests = ()
    with pytest.raises(TypeError):
        result[0].digests[0] = "poison"
    assert cache.symbol_summary(source, names) == result


def test_invalid_syntax_never_cached():
    cache = SourceAstSummaryCache()
    for _ in range(2):
        with pytest.raises(SyntaxError):
            cache.module_digest("def broken(:")
        with pytest.raises(SyntaxError):
            cache.symbol_summary("def broken(:", ("broken",))
    assert cache.info().entries == 0


def test_count_bytes_and_oversized_uncached():
    cache = SourceAstSummaryCache(max_entries=2, max_bytes=2048)
    for i in range(10):
        assert cache.module_digest(f"value = {i}") == digest(ast.parse(f"value = {i}"))
        assert cache.info().entries <= 2
        assert cache.info().retained_bytes <= 2048
    oversized = "#" + "x" * 4096 + "\nvalue = 1"
    before = cache.info()
    assert cache.module_digest(oversized) == digest(ast.parse(oversized))
    assert cache.module_digest(oversized) == digest(ast.parse(oversized))
    assert cache.info().entries == before.entries
    assert cache.info().retained_bytes == before.retained_bytes
    assert cache.info().misses == before.misses + 2


@pytest.mark.parametrize("options", [
    {"max_entries": 257}, {"max_entries": True},
    {"max_bytes": 32 * 1024 * 1024 + 1}, {"max_bytes": 1},
])
def test_configured_cache_cannot_exceed_hard_limits(options):
    with pytest.raises(ValueError, match="bounds"):
        SourceAstSummaryCache(**options)


def test_limits_cannot_be_changed_after_construction():
    cache = SourceAstSummaryCache()
    with pytest.raises(AttributeError):
        cache.max_bytes = 64 * 1024 * 1024
    with pytest.raises(AttributeError):
        cache.max_entries = 512


def test_concurrent_calls_preserve_results_and_bounds():
    cache = SourceAstSummaryCache(max_entries=16, max_bytes=8192)
    def read(i):
        source = f"def f(): return {i % 20}"
        result = cache.symbol_summary(source, ("f",))
        assert result[0].digests == (digest(ast.parse(source).body[0]),)
        assert cache.info().entries <= 16
        assert cache.info().retained_bytes <= 8192
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(read, range(200)))


@pytest.mark.parametrize("module_name,function_name,map_name,required_name", [
    ("backtest_strategy_fifty_certification", "certify_strategy_fifty_source", "STRATEGY50_SOURCE_AST", "REQUIRED_SOURCE_FILES"),
    ("backtest_strategy_fifty_seven_certification", "certify_strategy_fifty_seven_source", "STRATEGY57_SOURCE_AST", "REQUIRED_SOURCE_FILES"),
    ("backtest_strategy_fifty_eight_certification", "certify_strategy_fifty_eight_source", "STRATEGY58_SOURCE_AST", "REQUIRED_SOURCE_FILES"),
    ("backtest_strategy_liquidity_fade_certification", "certify_prepared_liquidity_fade_source", "LIQUIDITY_FADE_SOURCE_AST", None),
    ("backtest_strategy_entry_activity_certification", "certify_entry_activity_source", "ENTRY_ACTIVITY_SOURCE_AST", None),
])
def test_whole_module_callsite_fresh_read_rejects_same_path_mtime_mutation(
        monkeypatch, tmp_path, module_name, function_name, map_name, required_name):
    # Use a real shared Portfolio/OMS runtime source, under a bounded test seal.
    # Installed maps remain untouched pending their explicit review.
    module = importlib.import_module("src.backend." + module_name)
    relative = "src/trading_runtime/runtime.py"
    source = (Path(__file__).parents[1] / relative).read_text(encoding="utf-8")
    pin = digest(ast.parse(source))
    monkeypatch.setattr(module, map_name, {relative: pin})
    if required_name:
        monkeypatch.setattr(module, required_name, (relative,))
    file = tmp_path / "runtime.py"
    file.write_text(source, encoding="utf-8")
    verify = getattr(module, function_name)
    first = verify(source_overrides={relative: file})
    assert verify(source_overrides={relative: file}) == first
    stamp = file.stat()
    file.write_text(source + "\ndef forbidden_source_mutation(): return True\n", encoding="utf-8")
    os.utime(file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        verify(source_overrides={relative: file})
    file.write_text("def broken(:", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be parsed"):
        verify(source_overrides={relative: file})


@pytest.mark.parametrize("module_name,function_name,map_name,kinds", [
    ("backtest_fixed_v4_certification", "certify_rising_momentum_entry_source", "_RISING_MOMENTUM_REVIEWED_AST", (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
    ("backtest_strategy_profit_certification", "certify_profit_giveback_route_source", "REVIEWED_PROFIT_ROUTE", (ast.FunctionDef, ast.AsyncFunctionDef)),
])
def test_named_callsite_preserves_kind_filters_and_duplicate_rejection(
        monkeypatch, tmp_path, module_name, function_name, map_name, kinds):
    module = importlib.import_module("src.backend." + module_name)
    relative = "sample.py"
    file = tmp_path / relative
    source = "def guarded():\n    if True: raise ValueError('guard')\n"
    pin = digest(ast.parse(source).body[0])
    monkeypatch.setattr(module, map_name, {relative: {"guarded": pin}})
    file.write_text(source, encoding="utf-8")
    verify = getattr(module, function_name)
    first = verify(source_overrides={relative: file})
    assert verify(source_overrides={relative: file}) == first
    file.write_text(source + "\nclass guarded: pass\n", encoding="utf-8")
    if ast.ClassDef in kinds:
        with pytest.raises(ValueError, match="changed"):
            verify(source_overrides={relative: file})
    else:
        verify(source_overrides={relative: file})  # Existing profit filter ignores classes.
    file.write_text(source + "\nasync def guarded(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify(source_overrides={relative: file})


@pytest.mark.parametrize("module_name,function_name,map_name", [
    ("backtest_fixed_v4_certification", "certify_rising_momentum_entry_source", "_RISING_MOMENTUM_REVIEWED_AST"),
    ("backtest_strategy_profit_certification", "certify_profit_giveback_route_source", "REVIEWED_PROFIT_ROUTE"),
])
@pytest.mark.parametrize("shape", ["missing", "reordered", "extra"])
def test_named_callsite_rejects_missing_or_reordered_summary(
        monkeypatch, tmp_path, module_name, function_name, map_name, shape):
    module = importlib.import_module("src.backend." + module_name)
    file = tmp_path / "source.py"
    file.write_text("def first(): pass\ndef second(): pass\n", encoding="utf-8")
    first, second = map(digest, ast.parse(file.read_text()).body)
    monkeypatch.setattr(module, map_name, {"source.py": {"first": first, "second": second}})
    correct = (SymbolObservation("first", (first,)), SymbolObservation("second", (second,)))
    malformed = {"missing": correct[:1], "reordered": correct[::-1], "extra": correct + correct[:1]}[shape]
    monkeypatch.setattr(module, "canonical_symbol_ast_summary", lambda *a, **k: malformed)
    with pytest.raises(ValueError, match="changed"):
        getattr(module, function_name)(source_overrides={"source.py": file})


@pytest.mark.parametrize("number,name", [
    (42, "forty_two"), (50, "fifty"), (57, "fifty_seven"), (58, "fifty_eight"),
    (59, "fifty_nine"), (60, "sixty"), (61, "sixty_one"),
])
def test_installed_source_seal_rejects_helper_mutation_after_warming(tmp_path, number, name):
    module = importlib.import_module("src.backend.backtest_strategy_" + name + "_certification")
    verify = getattr(module, "certify_strategy_" + name + "_source")
    relative = "src/backend/source_ast_summary.py"
    assert relative in getattr(module, f"STRATEGY{number}_SOURCE_AST")
    proof = verify()
    assert verify() == proof
    source = (Path(__file__).parents[1] / relative).read_text(encoding="utf-8")
    changed = tmp_path / "source_ast_summary.py"
    changed.write_text(source, encoding="utf-8")
    assert verify(source_overrides={relative: changed}) == proof
    stamp = changed.stat()
    changed.write_text(source.replace("max_entries=256", "max_entries=512", 1), encoding="utf-8")
    os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        verify(source_overrides={relative: changed})
