import type { ReactNode } from "react";

export function MetaTag({ children, icon, mono = false }: { children: ReactNode; icon?: ReactNode; mono?: boolean }) {
  return <span className={`meta-tag${mono ? " meta-tag-mono" : ""}`}>{icon}{children}</span>;
}
