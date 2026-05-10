import type { ReactNode } from "react";

export function MetaTag({ children, mono = false }: { children: ReactNode; mono?: boolean }) {
  return <span className={`meta-tag${mono ? " meta-tag-mono" : ""}`}>{children}</span>;
}

