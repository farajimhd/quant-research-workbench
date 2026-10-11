"""Exact reader-binding and declared-risk delta restoration for legacy proof."""
import ast
from hashlib import sha256

CURRENT_MODULE = '00b0dcca7f519f669ff57a5dc6f554019eca6e95b13ab0849f75c485e745cc81'
PARENT_MODULE = '55beb538a169d525540792c488d8fc0f566a5e911802d498d2e461ee40f684dc'


def restore_native_session_execution_parent(source):
    """Prove the whole parent module after removing exactly two call bindings."""
    tree=ast.parse(source)
    digest=lambda node:sha256(ast.unparse(node).encode()).hexdigest()
    observed=digest(tree)
    if observed==PARENT_MODULE:
        return source
    if observed!=CURRENT_MODULE:
        raise ValueError('Native session execution differs from exact reviewed delta')
    removed=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):
            continue
        call_name=(node.func.id if isinstance(node.func,ast.Name) else
            'load_completed_risk_lookup' if ast.unparse(node.func)=='asyncio.to_thread'
            and node.args and ast.unparse(node.args[0])=='load_completed_risk_lookup' else None)
        selection={'build_certified_strategy_one_scheduler':('installed_session','selected_session'),
                   'load_completed_risk_lookup':('policy','manager.contract.confirmed_original_risk_policy')}
        if call_name not in selection:
            continue
        key,value=selection[call_name]
        matches=[k for k in node.keywords if k.arg==key and ast.unparse(k.value)==value]
        if len(matches)!=1:
            raise ValueError('Native session execution exact call binding differs')
        node.keywords.remove(matches[0])
        removed.append(call_name)
    if len(removed)!=2 or len(set(removed))!=2 or digest(tree)!=PARENT_MODULE:
        raise ValueError('Native session execution complete parent restoration differs')
    # Preserve formatting for downstream exact textual parent restorations.
    restored=source
    for current,parent in (
        ('        start_after_boundary_ms=start_after_boundary_ms,\n        installed_session=selected_session)',
         '        start_after_boundary_ms=start_after_boundary_ms)'),
        ('                    through_boundary_ms=through_boundary_ms,\n                    policy=manager.contract.confirmed_original_risk_policy)',
         '                    through_boundary_ms=through_boundary_ms)')):
        if restored.count(current)!=1:
            raise ValueError('Native session exact textual binding differs')
        restored=restored.replace(current,parent,1)
    if digest(ast.parse(restored))!=PARENT_MODULE:
        raise ValueError('Native session textual parent restoration differs')
    return restored
