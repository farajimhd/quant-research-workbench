"""Deterministic visual review of the real Trading Journal component."""


def review_journal_layout(page, screenshot_path):
    page.evaluate("""async () => {
        const {TradingJournalPreview} = await import('/src/features/canvas/tradingPresentation.tsx');
        const {default: React} = await import('/node_modules/.vite/deps/react.js');
        const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
        const host = document.createElement('div');
        document.querySelector('main').replaceChildren(host);
        const root = ReactDOM.createRoot(host);
        const data = {
            provider: 'review', mode: 'paper', complete: true, stale: false, as_of: '2026-09-17T13:40:00Z',
            positions: [], executions: [], orders: [], activity: [], position_lifecycles: [],
            performance_journal: {summary: {net_pnl: 125.5}, equity_curve: [
                {time: '2026-09-17T13:30:00Z', value: 0, drawdown: 0},
                {time: '2026-09-17T13:35:00Z', value: 180, drawdown: 0},
                {time: '2026-09-17T13:40:00Z', value: 125.5, drawdown: 54.5},
            ]},
        };
        ['GAIN', 'LOSS', 'UNKNOWN', 'FRACTION', 'LONGSYMBOL'].forEach((symbol, i) => {
            const instrument = {symbol, instrument_id: symbol};
            data.position_lifecycles.push({lifecycle_id: symbol, account_id: 'review', instrument,
                status: 'open', side: i === 1 ? 'SHORT' : 'LONG', current_quantity: i === 3 ? '12.5' : '1000',
                quantity: '1000', entry_price: '12.3456', opened_at: '2026-09-17T13:30:12Z',
                execution_ids: [], order_ids: []});
            data.positions.push({account_id: 'review', instrument, unrealized_pnl: i === 2 ? null : i === 1 ? -52.5 : 1234.56});
        });
        window.renderJournalReview = (mode) => root.render(React.createElement(TradingJournalPreview, {
            settings: {limit: 100, showRiskMultiple: true},
            data: mode === 'waiting' ? undefined : mode === 'empty' ? {...data, position_lifecycles: []} : data,
        }));
        window.renderJournalReview('populated');
    }""")
    journal = page.locator('.performance-journal')
    rows = journal.locator('.performance-active-position')
    rows.first.wait_for()
    assert rows.count() == 5
    assert '—' in rows.nth(2).inner_text(), 'Missing P&L must remain unavailable'
    assert '12.5' in rows.nth(3).inner_text(), 'Fractional quantity lost'
    assert journal.get_by_text('Edge snapshot', exact=True).count() == 0
    assert page.locator('.performance-overview-grid').evaluate("el => el.firstElementChild.matches('.performance-active-positions')")
    assert rows.first.evaluate('el => el.scrollWidth <= el.clientWidth + 1'), 'Position row overflows'
    page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem + '__positions.png')), full_page=True)
    rows.first.focus()
    page.keyboard.press('Enter')
    page.get_by_role('dialog').wait_for()
    page.keyboard.press('Escape')
    page.get_by_role('dialog').wait_for(state='hidden')
    journal.get_by_role('tab', name='Positions 0').click()
    journal.get_by_placeholder('Search positions, symbols, setups, exits…').wait_for()
    journal.get_by_role('tab', name='Overview 0').click()
    for mode, message in [('empty', 'No open positions.'), ('waiting', 'Waiting for position data.')]:
        page.evaluate('(mode) => window.renderJournalReview(mode)', mode)
        journal.get_by_text(message, exact=True).wait_for()
        page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem + f'__{mode}.png')), full_page=True)
    page.evaluate("window.renderJournalReview('populated')")
    rows.first.wait_for()
