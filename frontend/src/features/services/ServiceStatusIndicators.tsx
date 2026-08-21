import { AlertTriangle, CheckCircle2, WifiOff } from "lucide-react";

import type { ServiceStatusPayload } from "./contracts";
import { statusInfo } from "./statusPresentation";

export function ServiceIcon({ service }: { service: ServiceStatusPayload }) {
  const info = statusInfo(service);
  const Icon = !service.online ? WifiOff : info.tone === "error" || info.tone === "warn" ? AlertTriangle : CheckCircle2;
  return <Icon className="service-card-icon" size={20} />;
}

export function ServiceStatusBadge({ online, status }: { online: boolean; status: string }) {
  const info = statusInfo({ online, status } as ServiceStatusPayload);
  return <span className={`service-status-badge ${info.className} ${info.tone}`} title={info.description}>{info.label}</span>;
}
