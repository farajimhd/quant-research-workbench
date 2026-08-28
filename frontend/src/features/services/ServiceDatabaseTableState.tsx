import { useState } from "react";

import { api } from "../../api/client";
import { DataTable } from "../../app/components/DataTable";
import { Modal } from "../../app/components/Modal";
import { displayName } from "../../app/format";
import type { ServiceDatabaseTableRow, ServiceStatusPayload, ServiceTablePreviewPayload } from "./contracts";
import { databaseClass, serviceTableStateYears, shortTableTimestamp, tableStateClass } from "./diagnostics";

export function ServiceDatabaseTableState({ service }: { service: ServiceStatusPayload }) {
  const rows = service.database_tables?.rows ?? [];
  const error = service.database_tables?.error || "";
  const [preview, setPreview] = useState<ServiceTablePreviewPayload | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const years = serviceTableStateYears();

  async function openPreview(row: ServiceDatabaseTableRow) {
    if (!row.database || !row.table || row.database === "-" || row.table === "-") return;
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const payload = await api<ServiceTablePreviewPayload>(`/api/services/${service.registry.id}/tables/${encodeURIComponent(row.database)}/${encodeURIComponent(row.table)}/preview?limit=20`);
      setPreview(payload);
    } catch (exc) {
      setPreviewError(exc instanceof Error ? exc.message : String(exc));
      setPreview({ database: row.database, limit: 20, rows: [], table: row.table });
    } finally {
      setPreviewLoading(false);
    }
  }

  if (error && !rows.length) {
    return (
      <div className="service-db-state-empty error">
        <strong>Database table state unavailable</strong>
        <span>{error}</span>
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="service-db-state-empty">
        <strong>No direct database table contract reported.</strong>
        <span>This service has no table state configured for the dashboard.</span>
      </div>
    );
  }
  return (
    <>
      <div className="service-db-state-wrap">
        <table className="service-db-state-table">
          <colgroup>
            <col className="service-db-state-col-status" />
            <col className="service-db-state-col-role" />
            <col className="service-db-state-col-table" />
            <col className="service-db-state-col-latest" />
            <col className="service-db-state-col-count" />
            <col className="service-db-state-col-count" />
            <col className="service-db-state-col-recent" />
            <col className="service-db-state-col-recent" />
            {years.map((year) => <col className="service-db-state-col-year" key={year} />)}
          </colgroup>
          <thead>
            <tr>
              <th>Status</th><th>Role</th><th>Table</th><th>Latest</th><th>Total</th><th>Today</th><th>7d</th><th>30d</th>
              {years.map((year) => <th key={year}>{year}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr className={`service-db-state-row ${tableStateClass(row.status)}`} key={`${row.database}.${row.table}.${index}`}>
                <td><span className="service-db-state-status">{displayName(row.status || "unknown")}</span></td>
                <td title={row.role || ""}>{row.role || "-"}</td>
                <td title={`${row.database || "-"}.${row.table || "-"}${row.time_column && row.time_column !== "-" ? ` by ${row.time_column}` : ""}`}><button className="table-primary-link" onClick={() => void openPreview(row)} type="button">
                  <span className={`service-db-name ${databaseClass(row.database)}`}>{row.database || "-"}</span>
                  <span className="service-db-dot">.</span>
                  <span className="service-db-table-name">{row.table || "-"}</span>
                </button></td>
                <td title={row.latest_update || ""}>{shortTableTimestamp(row.latest_update)}</td>
                <td className="service-db-total-cell">{row.rows || "-"}</td>
                <td className="service-db-today-cell">{row.rows_today || "-"}</td>
                <td className="service-db-muted-count-cell">{row.rows_last_week || "-"}</td>
                <td className="service-db-muted-count-cell">{row.rows_last_month || "-"}</td>
                {years.map((year) => <td className="service-db-muted-count-cell" key={year}>{row[`rows_${year}`] || "-"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {error ? <p className="service-db-state-error">{error}</p> : null}
      </div>
      {preview || previewLoading ? (
        <ServiceTablePreviewModal error={previewError} loading={previewLoading} onClose={() => { setPreview(null); setPreviewError(""); }} payload={preview} service={service} />
      ) : null}
    </>
  );
}

function ServiceTablePreviewModal({ error, loading, onClose, payload, service }: { error: string; loading: boolean; onClose: () => void; payload: ServiceTablePreviewPayload | null; service: ServiceStatusPayload }) {
  const title = payload ? `${payload.database}.${payload.table}` : "Table Preview";
  const subtitle = payload?.order_by ? `Latest ${payload.limit} rows ordered by ${payload.order_by}` : `Latest ${payload?.limit ?? 20} rows`;
  return (
    <Modal className="service-table-preview-modal-panel" onClose={onClose} title={`${service.registry.label} Table Preview`}>
      <div className="service-table-preview">
        <div className="service-table-preview-header">
          <div><span className="service-table-preview-kicker">Direct ClickHouse Preview</span><h3>{title}</h3><p>{subtitle}</p></div>
          {loading ? <span className="service-table-preview-loading inline-loading-message"><span className="loading-spinner" aria-hidden="true" />Loading...</span> : null}
        </div>
        {error ? <div className="service-table-preview-error">{error}</div> : null}
        <DataTable empty={loading ? "Loading table rows..." : "No preview rows returned."} fitToContent rows={payload?.rows ?? []} title="Last 20 Rows" />
      </div>
    </Modal>
  );
}
