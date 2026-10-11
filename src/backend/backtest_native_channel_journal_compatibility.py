"""Exact native journal delta restoration for existing legacy projections."""
import ast
from hashlib import sha256

CURRENT_CLASS = 'd4fa3fac66dd86b6abf3cb5fe37b2e870023e6ea900fb0be9a443e135530ceca'
PARENT_CLASS = '01914e8ce460382054490ea0a0dc077855a30c8fa9835be64c257583d9db434f'
ADDITIONS = frozenset((
    '0aa7f5d969d3c28eaa80ce49605dc6b9f97ec288d5cd1ab74b1118807b4e17fe',
    'c55f9bf0935a263abcf6a85f585ef20dcd161dd2bff4b2e350e6529769cc1d79',
    '23a0b50f20329d2857a831e322853103df735ae715b08e5fa3733362255c6fb7',
    '2c3d35ff1d060d8bb5e84981cb825afe8ae61c526375d23f3ae701ba19788463',
    'd5c0f4a5a831e66b28d5f163aa876a9bfd3e03580b77ea6ed0a6be35efca02ac',
))


def restore_native_channel_journal_class(source):
    """Return the proven parent class; never accept an unknown native delta."""
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node,ast.ClassDef)
               and node.name == 'BacktestMemoryJournal']
    if len(classes) != 1:
        raise ValueError('Native journal compatibility requires exactly one class')
    digest = lambda node: sha256(ast.unparse(node).encode()).hexdigest()
    observed = digest(classes[0])
    if observed == PARENT_CLASS:
        return classes[0]
    if observed != CURRENT_CLASS:
        raise ValueError('Native journal current source differs from exact reviewed delta')
    removed = []
    class Restore(ast.NodeTransformer):
        def generic_visit(self,node):
            if isinstance(node,ast.stmt) and digest(node) in ADDITIONS:
                removed.append(digest(node))
                return None
            return super().generic_visit(node)
    restored = Restore().visit(classes[0])
    if len(removed) != len(ADDITIONS) or set(removed) != ADDITIONS or digest(restored) != PARENT_CLASS:
        raise ValueError('Native journal complete legacy restoration differs')
    return restored
