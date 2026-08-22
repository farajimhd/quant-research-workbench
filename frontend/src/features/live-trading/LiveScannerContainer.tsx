import { DataTable, type BackendQueryPreset, type BackendTableQuery } from "../../app/components/DataTable";
import type { ScannerSnapshot, SignalRow } from "./contracts";
import { marketStateTableColumns } from "./scanner";
import type { ScannerQueryGroup } from "./liveWorkspaceContracts";
import { stringValue } from "./liveTradingFormat";

export const LIVE_SCANNER_COLUMNS = [
  "ticker",
  "bar_time_market",
  "minute_of_day",
  "current_open",
  "last_close",
  "last_open",
  "last_high",
  "last_low",
  "last_vwap",
  "last_day_high_so_far",
  "last_day_low_so_far",
  "last_day_volume_so_far",
  "last_day_dollar_volume_so_far",
  "last_day_open",
  "last_gap_pct",
  "last_return_5",
  "last_volume",
  "last_recent_volume_5",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "last_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence_score",
  "current_open_above_last_2_body_high",
  "spread_bps_abs",
];

export const LIVE_SIGNAL_COLUMNS = [
  "ticker",
  "live_news_recency",
  "bar_time_market",
  "live_signal_time",
  "current_open",
  "last_volume",
  "last_return_5",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "live_signal_query",
  "last_close",
  "last_day_volume_so_far",
  "last_day_max_change_pct",
  "last_day_current_change_pct",
  "last_vwap",
  "live_bias",
  "live_reasons",
  "live_risks",
];

export type LiveScannerContainerProps = {
  loading: boolean;
  marketEmptyMessage: string;
  marketRows: Record<string, unknown>[];
  marketSnapshot: ScannerSnapshot | null;
  query: BackendTableQuery;
  queryGroups: ScannerQueryGroup[];
  queryName: string;
  rows: Record<string, unknown>[];
  selectedTicker: string;
  signalRows: SignalRow[];
  snapshot: ScannerSnapshot | null;
  onDeleteQueryGroup: (id: string) => void;
  onQueryChange: (query: BackendTableQuery) => void;
  onQueryNameChange: (value: string) => void;
  onRowSelect: (row: Record<string, unknown>) => void;
  onSaveQueryGroup: (name: string, query: BackendTableQuery) => void;
};

export function LiveScannerContainer({
  loading,
  marketEmptyMessage,
  marketRows,
  marketSnapshot,
  onDeleteQueryGroup,
  onQueryChange,
  onQueryNameChange,
  onRowSelect,
  onSaveQueryGroup,
  query,
  queryGroups,
  queryName,
  rows,
  selectedTicker,
  signalRows,
  snapshot,
}: LiveScannerContainerProps) {
  const queryPresets: BackendQueryPreset[] = queryGroups.map((group) => ({ id: group.id, label: group.name, query: group.query }));
  return (
    <div className="live-scanner-stack">
      <section className="live-scanner-table live-scanner-signals">
        <DataTable
          backendQuery={{
            columns: snapshot?.columns?.length ? snapshot.columns : LIVE_SCANNER_COLUMNS,
            loading,
            onChange: onQueryChange,
            onDeletePreset: onDeleteQueryGroup,
            onNameChange: onQueryNameChange,
            onSavePreset: onSaveQueryGroup,
            presets: queryPresets,
            queryName,
            value: query,
          }}
          columns={LIVE_SIGNAL_COLUMNS}
          defaultSort={{ column: "live_signal_time", direction: "desc" }}
          empty={loading ? "Loading scanner..." : "No scanner signals detected yet."}
          fitToContent
          isRowSelected={(row) => stringValue(row, "ticker") === selectedTicker}
          onRowClick={onRowSelect}
          preserveFiltersOnDataChange
          rows={signalRows}
          title={`Signals${rows.length ? ` (${rows.length} current)` : ""}`}
          transposeHelper
        />
      </section>
      <section className="live-scanner-table live-scanner-market">
        <DataTable
          columns={marketStateTableColumns(marketSnapshot?.columns ?? [])}
          defaultSort={{ column: "last_day_volume_so_far", direction: "desc" }}
          empty={loading ? "Loading market state..." : marketEmptyMessage}
          isRowSelected={(row) => stringValue(row, "ticker") === selectedTicker}
          onRowClick={onRowSelect}
          preserveFiltersOnDataChange
          rows={marketRows}
          title="Market State"
          transposeHelper
        />
      </section>
    </div>
  );
}
