import type { ReactNode } from "react";

import { MetaTag } from "./MetaTag";

export function CountChip({ icon, label, value }: { icon?: ReactNode; label?: string; value?: number | string }) {
  const text = value === undefined ? (label ?? "") : label ? `${value} ${label}` : String(value);
  return <MetaTag icon={icon}>{text}</MetaTag>;
}

