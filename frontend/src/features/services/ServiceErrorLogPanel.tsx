import { useState } from "react";

import { Modal } from "../../app/components/Modal";
import { displayName } from "../../app/format";
import type { ServiceStatusPayload } from "./contracts";
import {
  collectErrorLogItems,
  logStatusFilterOptions,
  unpackLogDetail,
  type ServiceLogItem,
  type ServiceLogStatusFilter,
} from "./diagnostics";
import { ServicePanel as Panel } from "./ServicePanel";
import { ServiceStatusBadge } from "./ServiceStatusIndicators";

export function ServiceErrorLogPanel({ pageError, service }: { pageError: string; service: ServiceStatusPayload }) {
  const items = collectErrorLogItems(pageError, service);
  const [statusFilter, setStatusFilter] = useState<ServiceLogStatusFilter>("all");
  const [selectedLog, setSelectedLog] = useState<ServiceLogItem | null>(null);
  const filteredItems = statusFilter === "all" ? items : items.filter((item) => item.status === statusFilter);
  const activeItems = items.filter((item) => item.status === "active" || item.status === "retrying");
  const logPath = service.logs?.path || "";
  const logError = service.logs?.error || "";
  const tableRows = filteredItems.length ? filteredItems : [{ detail: "No log rows match the selected status filter.", key: "service", status: "clear" as const, title: "No matching rows" }];
  return (
    <Panel title="Errors And Logs">
      <div className={`service-log-panel ${activeItems.length ? "has-active" : ""}`}>
        <div className="service-log-summary">
          <ServiceStatusBadge online={service.online} status={activeItems.length ? "degraded" : "running"} />
          <div>
            <strong>{items.length ? `${items.length} log row${items.length === 1 ? "" : "s"} loaded` : "No service log rows reported"}</strong>
            <p>{logPath ? `Source: ${logPath}` : "No saved runtime log path was reported by this service."}{logError ? ` (${logError})` : ""}</p>
          </div>
        </div>
        <div className="service-log-filter" aria-label="Filter service logs by status">
          {logStatusFilterOptions(items).map((option) => (
            <button className={statusFilter === option.status ? "active" : ""} key={option.status} onClick={() => setStatusFilter(option.status)} type="button">
              <span>{displayName(option.status)}</span><strong>{option.count}</strong>
            </button>
          ))}
        </div>
        <div className="service-log-table-wrap">
          <table className="service-log-table">
            <thead><tr><th>Time</th><th>Status</th><th>Source</th><th>Event</th><th>Message</th><th>Detail</th></tr></thead>
            <tbody>
              {tableRows.map((item, index) => (
                <tr
                  className={`service-log-row ${item.status}`}
                  key={`${item.key}-${index}`}
                >
                  <td className="service-log-time" title={item.time || item.meta || ""}>{item.time || "-"}</td>
                  <td><span className={`service-log-status ${item.status}`}>{displayName(item.status)}</span></td>
                  <td title={item.source || item.meta || ""}>{item.source || "-"}</td>
                  <td title={displayName(item.event || item.key)}>{displayName(item.event || item.key)}</td>
                  <td title={item.title}><button className="table-primary-link" onClick={() => setSelectedLog(item)} type="button">{item.title}</button></td>
                  <td title={item.detail}>{item.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {selectedLog ? (
        <Modal className="service-log-detail-modal-panel" onClose={() => setSelectedLog(null)} title="Service Log Row">
          <ServiceLogDetail item={selectedLog} />
        </Modal>
      ) : null}
    </Panel>
  );
}

function ServiceLogDetail({ item }: { item: ServiceLogItem }) {
  const detailRows = unpackLogDetail(item.detail);
  const rows = [
    { key: "time", value: item.time || "-" },
    { key: "status", value: displayName(item.status) },
    { key: "source", value: item.source || "-" },
    { key: "event", value: displayName(item.event || item.key) },
    { key: "message", value: item.title || "-" },
    { key: "metadata", value: item.meta || "-" },
    { key: "row_key", value: item.key || "-" },
  ];
  return (
    <div className="service-log-detail">
      <div className={`service-log-detail-status ${item.status}`}><span>{displayName(item.status)}</span><strong>{item.title || displayName(item.event || item.key)}</strong></div>
      <dl className="service-log-detail-grid">
        {rows.map((row) => <div className={row.key === "detail" || row.key === "message" ? "wide" : ""} key={row.key}><dt>{displayName(row.key)}</dt><dd>{row.value}</dd></div>)}
      </dl>
      <section className="service-log-detail-fields">
        <div className="service-log-detail-section-title"><span>Detail Fields</span><strong>{detailRows.length}</strong></div>
        <dl className="service-log-detail-grid">
          {(detailRows.length ? detailRows : [{ key: "detail", value: item.detail || "-" }]).map((row) => <div className="wide" key={row.key}><dt>{displayName(row.key)}</dt><dd>{row.value}</dd></div>)}
        </dl>
      </section>
    </div>
  );
}
