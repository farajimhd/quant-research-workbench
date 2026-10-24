"""Read-only score-column adapter; deploy beside the observer, never into a live optimizer snapshot."""
from rich.panel import Panel
from rich.table import Table


def ranking_table(ui, data, width, height, mode, selection):
    rows = ui.canvas.ordered_rows(data.get('rows', []), mode)
    size = ui.canvas.ranking_size(height)
    selection = min(selection, max(0, len(rows) - 1))
    start = selection // size * size
    table = Table(expand=True, padding=(0, 0), show_edge=False)
    compact = width < 105
    for name in ('Rank', 'Serial' if compact else 'Origin %, M/X / serial',
                 'Net P&L $', 'Broad', 'Specialist', 'Win %', 'Positions'):
        table.add_column(name, justify='left' if name in ('Serial', 'Origin %, M/X / serial') else 'right',
                         no_wrap=True, overflow='ellipsis')
    for i, row in enumerate(rows[start:start + size], start):
        m = row['metrics']
        label = str(row['serial']) if compact else ui.candidate_label(row)
        table.add_row(str(row['rank']), label, f"{m['net_pnl']:,.2f}", f"{row['score']:.5f}", f"{row['specialist_score']:.4f}",
                      '-' if m['win_rate'] is None else f"{100 * m['win_rate']:.1f}",
                      str(int(m['closed_positions'])), style='bold cyan' if i == selection else None)
    return table, len(rows)


def install(ui):
    original_load = ui.canvas.load_generation
    def load(folder, generation):
        import json
        from pathlib import Path
        rows = original_load(folder, generation)
        scores = json.loads((Path(folder) / f'generation-{generation:04d}' / 'scores.json').read_bytes())
        if len(rows) != len(scores['specialist_scores']):
            raise ValueError('Specialist display alignment changed')
        for row, serial, value, target in zip(rows, scores['ids'], scores['specialist_scores'], scores['specialist_targets']):
            if row['serial'] != serial:raise ValueError('Specialist display identity changed')
            row.update(specialist_score=value, specialist_target=target)
        return rows
    ui.canvas.load_generation = load
    original_order = ui.canvas.ordered_rows
    def ordered(rows, mode):
        if mode == 'specialist':
            return sorted(rows, key=lambda r: (-r['specialist_score'], r['serial']))
        return original_order(rows, mode)
    ui.canvas.ordered_rows = ordered
    original_render = ui.render
    original_details = ui.detail_lines

    def render(data, width, height, mode, selection, view, stage, page):
        layout = original_render(data, width, height, mode, selection, view, stage, page)
        if not view.startswith('families'):
            table, count = ranking_table(ui, data, width, height, mode, selection)
            layout['leaders'].update(Panel(table,
                title=f"Sealed generation {data.get('generation', 0)} | {count} candidates | {mode}"))
            layout['keys'].update('1 profit | 2 broad | 5 specialist | 3 win | 4 risk | j/k select | n/b page | f/o/p details | q close')
        return layout

    def details(row, payload, view, stage):
        if row and view == 'objective':
            return [f"Broad {row['score']:.6f} | Specialist {row['specialist_score']:.6f} | eligible: {row['eligible']}",
                    f"Best specialist case: {row['specialist_target']}",
                    'Budget $1000/case; positive cap 1; outside loss 1; broad loss 1; worst 10% weight 0.25.',
                    'Case utility = (net profit - 0.05 x realized case drawdown) / $1000.',
                    'No winning-trade trimming. Both scores dimensionless; higher is better.']
        return original_details(row, payload, view, stage)

    ui.render = render
    ui.detail_lines = details
