"""Existing Phase-2 canvas with bounded CPU evolution summaries."""
from research.vectorized_backtest.v27.terminal import CampaignProgress
from research.vectorized_backtest.v27.terminal import render as original_render,ranking_page_size as original_page_size
from rich.text import Text

def ranking_page_size(height,status=None):return original_page_size(height-1,status)
def render(status,**kwargs):
    layout=original_render(status,**kwargs);e=status.get('evolution',{})
    nodes=e.get('neurons',{});depth=e.get('depth',{});rec=e.get('recurrent_edges',{})
    compact=kwargs.get('width',110)<110
    detail=(f"Species {e.get('species_count','—')} stale {e.get('stagnant_species','—')} | neurons {nodes.get('median','—')}/{nodes.get('max','—')} | depth {depth.get('max','—')} | rec {rec.get('max','—')}" if compact else f"Species {e.get('species_count','—')} | stagnant {e.get('stagnant_species','—')} | neurons median/max {nodes.get('median','—')}/{nodes.get('max','—')} | depth max {depth.get('max','—')} | recurrence max {rec.get('max','—')} | CPU species {e.get('species_seconds',0):.3f}s")
    layout['header'].size=3
    layout['header'].update(Text(f"V31 {status.get('status','starting').upper()} | NET PROFIT ONLY | PHASE 2 GLOBAL\nValidation: display only; no selection feedback\n{detail}"))
    return layout
