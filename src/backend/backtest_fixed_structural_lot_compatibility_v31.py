"""Exact reviewed publication-source successor restoration to retained110."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {}
APPROVED_METADATA_ANCHOR = '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
APPROVED_SELF_AST = 'bb5a66c857668375f096e517981409bcb2397bb2c510119cf9fffdf53dccf1b0'

def restore_reviewed_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    names = ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        declarations = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1 and (type(n.targets[0]) is ast.Name) and (n.targets[0].id == name)]
        if len(declarations) != 1:
            raise ValueError('Declared saved-preparation compatibility declaration shape differs')
        fresh[name] = ast.literal_eval(declarations[0].value)
    if fresh != dict(zip(names, (REVIEWED_EDITS, APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST))):
        raise ValueError('Declared saved-preparation compatibility loaded and fresh metadata differs')
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and (type(node.targets[0]) is ast.Name):
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Declared saved-preparation compatibility envelope differs')
    if sha256(json.dumps(REVIEWED_EDITS, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Declared saved-preparation compatibility metadata anchor differs')
    recipe = REVIEWED_EDITS.get(relative.removeprefix('src/'))
    if recipe is None:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Declared saved-preparation compatibility changed during restoration')
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Declared saved-preparation compatibility changed during restoration')
        return source
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('Declared saved-preparation compatibility exact reviewed edit differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('Declared saved-preparation compatibility retained parent AST differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('Declared saved-preparation compatibility changed during restoration')
    return restored
