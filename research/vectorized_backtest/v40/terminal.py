"""Existing Phase-2 canvas with bounded CPU evolution summaries."""
from research.vectorized_backtest.v27.terminal import CampaignProgress as _CampaignProgress
from research.vectorized_backtest.v27.terminal import render as original_render,ranking_page_size as original_page_size
from rich.text import Text
from .publication import write_json


class CampaignProgress(_CampaignProgress):
    """Keep the canvas, use V40's atomic writer with bounded Windows retries."""
    def _publish(self):
        import json
        snapshot=self.snapshot();write_json(self.output/'status.json',snapshot)
        if self.live is not None:
            self.live.update(lambda width,height:render(snapshot,width=width,height=height))
        with self.lock:events=list(self.events);self.events.clear()
        if events:
            with (self.output/'events.jsonl').open('a',encoding='utf-8') as stream:
                for event in events:stream.write(json.dumps(event)+'\n')
            if not self.console.is_terminal:
                self.console.print(f"V40 {snapshot['status']} | {snapshot['stage']} | generations {snapshot['completed_generations']}/{snapshot['config']['generations']} | sessions {snapshot['completed_sessions']}/{snapshot['config']['training_sessions']}")

def ranking_page_size(height,status=None):return original_page_size(height-1,status)
def render(status,**kwargs):
    layout=original_render(status,**kwargs);e=status.get('evolution',{})
    nodes=e.get('neurons',{});depth=e.get('depth',{});rec=e.get('recurrent_edges',{})
    compact=kwargs.get('width',110)<110
    detail=(f"Species {e.get('species_count','—')} stale {e.get('stagnant_species','—')} | neurons {nodes.get('median','—')}/{nodes.get('max','—')} | depth {depth.get('max','—')} | rec {rec.get('max','—')}" if compact else f"Species {e.get('species_count','—')} | stagnant {e.get('stagnant_species','—')} | neurons median/max {nodes.get('median','—')}/{nodes.get('max','—')} | depth max {depth.get('max','—')} | recurrence max {rec.get('max','—')} | CPU species {e.get('species_seconds',0):.3f}s")
    layout['header'].size=3
    layout['header'].update(Text(f"V40 {status.get('status','starting').upper()} | V21 OBJECTIVE | PHASE 2 GLOBAL\nValidation: display only; no selection feedback\n{detail}"))
    return layout
