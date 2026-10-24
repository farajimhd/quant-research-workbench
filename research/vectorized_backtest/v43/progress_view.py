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
                 'Net P&L $', 'Score $', 'Win %', 'Positions'):
        table.add_column(name, justify='left' if name in ('Serial', 'Origin %, M/X / serial') else 'right',
                         no_wrap=True, overflow='ellipsis')
    for i, row in enumerate(rows[start:start + size], start):
        m = row['metrics']
        label = str(row['serial']) if compact else ui.candidate_label(row)
        table.add_row(str(row['rank']), label, f"{m['net_pnl']:,.2f}", f"{row['score']:,.2f}",
                      '-' if m['win_rate'] is None else f"{100 * m['win_rate']:.1f}",
                      str(int(m['closed_positions'])), style='bold cyan' if i == selection else None)
    return table, len(rows)


def install(ui):
    original_render = ui.render
    original_details = ui.detail_lines

    def render(data, width, height, mode, selection, view, stage, page):
        layout = original_render(data, width, height, mode, selection, view, stage, page)
        if not view.startswith('families'):
            table, count = ranking_table(ui, data, width, height, mode, selection)
            layout['leaders'].update(Panel(table,
                title=f"Sealed generation {data.get('generation', 0)} | {count} candidates | {mode}"))
        return layout

    def details(row, payload, view, stage):
        if row and view == 'objective':
            return [f"Saved V43 score ${row['score']:,.2f} | parent eligible: {row['eligible']}",
                    'Score = net profit - trade removal - 0.05 x drawdown.',
                    'Remove top 10% of positive contributors, rounded up; retain every loss.',
                    'Ticker concentration is diagnostic only; session penalty unavailable here. Higher is better.']
        return original_details(row, payload, view, stage)

    ui.render = render
    ui.detail_lines = details
