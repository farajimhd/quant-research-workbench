import { ArrowUpRight } from "lucide-react";

import { MetricRatio } from "../../app/components/MetricRatio";
import { displayName } from "../../app/format";
import type { ServiceReadinessDimension, ServiceStatusPayload } from "./contracts";
import { serviceFleetDatabaseSummary, serviceFleetMetrics } from "./fleetPresentation";
import { ServiceIcon, ServiceStatusBadge } from "./ServiceStatusIndicators";
import { cardMessage, phaseText, serviceFreshness, statusInfo } from "./statusPresentation";

export function ServiceFleetCard({ now, onOpen, service }: { now: Date; onOpen: () => void; service: ServiceStatusPayload }) {
  const info = statusInfo(service);
  const freshness = serviceFreshness(service, now);
  const metrics = serviceFleetMetrics(service);
  const database = serviceFleetDatabaseSummary(service);
  const focus = serviceFleetFocus(service);
  return (
    <article className={`service-fleet-card ${info.className}`}>
      <button aria-label={`Open ${service.registry.label} details`} className="service-fleet-open" onClick={onOpen} type="button">
        <div className="service-fleet-card-header">
          <div className="service-fleet-identity">
            <ServiceIcon service={service} />
            <div><h2>{service.registry.label}</h2></div>
          </div>
          <div className="service-fleet-state">
            <ServiceStatusBadge status={service.status} online={service.online} />
            {service.online ? <span className={`service-fleet-freshness ${freshness.tone}`}>{freshness.label}</span> : null}
          </div>
          <div className="service-fleet-focus">
            <strong>{focus.phase}</strong>
            <p title={focus.message}>{focus.message}</p>
          </div>
        </div>

        <div className="service-fleet-metrics" aria-label={`${service.registry.label} objective metrics`}>
          {metrics.map((metric, index) => (
            <div className={`service-fleet-metric tone-${metric.tone ?? "neutral"}`} key={metric.label}>
              <span>{metric.label}</span>
              <strong className={`service-fleet-metric-value${metric.valueParts ? " is-ratio" : ""}`}>
                {metric.valueParts ? <MetricRatio accent={(index % 4 + 1) as 1 | 2 | 3 | 4} current={metric.valueParts.current} total={metric.valueParts.total} /> : metric.value}
              </strong>
              <small title={metric.detail}>{metric.detail}</small>
            </div>
          ))}
        </div>

        <div className={`service-fleet-database ${database.tone}`}>
          <div><span>Database · {database.product}</span><strong>{database.statusParts ? <MetricRatio accent={1} {...database.statusParts} /> : database.status}</strong></div>
          <div><span>Today</span><strong>{database.today}</strong></div>
          <div><span>Overall</span><strong>{database.overall}</strong></div>
          <div><span>Latest</span><strong>{database.latest}</strong></div>
        </div>
        {service.readiness ? <div className="service-readiness-strip" aria-label={`${service.registry.label} readiness dimensions`}>
          {([
            ["Live", service.readiness.liveness],
            ["Dependencies", service.readiness.dependencies],
            ["Data", service.readiness.data],
            ["Execution", service.readiness.execution],
          ] as Array<[string, ServiceReadinessDimension]>).map(([label, dimension]) => <span className={`tone-${readinessTone(dimension.status)}`} key={label} title={`${dimension.evidence} Source: ${dimension.source}`}><small>{label}</small><strong>{displayName(dimension.status)}</strong></span>)}
        </div> : null}
        <ArrowUpRight aria-hidden="true" className="service-fleet-open-icon" size={13} />
      </button>
    </article>
  );
}

function serviceFleetFocus(service: ServiceStatusPayload) {
  if (!service.online) {
    return { phase: "No heartbeat", message: "Endpoint timed out · verify process and bind." };
  }
  if (service.registry.id === "qmd-history") {
    return { phase: "Ready to serve", message: "Canonical history queries, deterministic streams, and event-derived bars are available." };
  }
  return { phase: displayName(phaseText(service)), message: cardMessage(service) };
}

function readinessTone(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "ready") return "ok";
  if (["blocked", "degraded", "offline"].includes(normalized)) return "warn";
  return "neutral";
}
