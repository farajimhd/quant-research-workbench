export type ActivityHistoryPage = { complete?: boolean; next_offset?: number | null };

/** Read every cursor page, serially, without treating an empty page as EOF. */
export async function loadActivityHistory<T>(
  initial: ActivityHistoryPage,
  fetchPage: (offset: number) => Promise<ActivityHistoryPage & { rows: T[] }>,
  acceptPage: (page: ActivityHistoryPage & { rows: T[] }) => void,
  signal: AbortSignal,
) {
  let page = initial;
  let previousOffset = -1;
  while (!page.complete && !signal.aborted) {
    const offset = page.next_offset;
    if (!Number.isSafeInteger(offset) || Number(offset) < 0 || Number(offset) <= previousOffset) {
      throw new Error("Strategy activity pagination did not advance. Retry loading the history.");
    }
    previousOffset = Number(offset);
    const next = await fetchPage(Number(offset));
    if (signal.aborted) return;
    acceptPage(next);
    page = next;
  }
}

/** Detailed evidence is fetched by record_id when an event is inspected. */
export function activitySummaryRow(row: Record<string, unknown>): Record<string, unknown> {
  const { gate_snapshot, event_evidence, management_event, decision_evidence, ...summary } = row;
  return summary;
}
