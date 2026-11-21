"""V17 label adapter for the established read-only validation progress panel."""
from ..phase1_v16 import panel

base_render=panel.render


def render(state, active, now=None):
    result=base_render(state,active,now)
    result.title=f"Phase1 V17 | {state.get('state','waiting')}"
    return result


panel.render=render
if __name__=='__main__':panel.main()
