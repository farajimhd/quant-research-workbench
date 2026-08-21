import type { ReactNode } from "react";

export function ServicePanel({ children, className = "", title }: { children: ReactNode; className?: string; title: string }) {
  return (
    <section className={`service-panel ${className}`}>
      {title ? <div className="service-panel-header"><h2>{title}</h2></div> : null}
      {children}
    </section>
  );
}
