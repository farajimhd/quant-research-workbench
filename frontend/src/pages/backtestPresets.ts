export type BacktestTickerPreset = 'SUGP' | 'JUNS' | 'both' | 'all' | 'custom' | 'market';
export type ValidationBook = { id: string; ticker: string; start: string; end: string; version: string; selection_contract?: string };
export const DEFAULT_BACKTEST_DATE = '2026-08-21';

export function v6BookFor(ticker: string, date: string, books: ValidationBook[]) {
  return books.filter(book => book.ticker === ticker && book.version === 'causal-level-book-v7-mle-1'
    && book.start <= date && date <= book.end)
    .sort((a,b) => b.end.localeCompare(a.end) || Number(b.selection_contract==='symmetric-level-evidence-selection-2')-Number(a.selection_contract==='symmetric-level-evidence-selection-2') || a.start.localeCompare(b.start) || a.id.localeCompare(b.id))[0];
}

export function presetTickers(preset: BacktestTickerPreset, date: string, books: ValidationBook[]) {
  if (preset === 'both') return ['SUGP', 'JUNS'];
  if (preset === 'all') return [...new Set(books.filter(b => v6BookFor(b.ticker,date,books)).map(b => b.ticker))].sort();
  return preset === 'custom' || preset === 'market' ? [] : [preset];
}

export function tickerWindow(ticker: string) {
  return ticker === 'JUNS' ? {start:'07:00:00',end:'07:30:00'} : {start:'04:00:00',end:'04:30:00'};
}
