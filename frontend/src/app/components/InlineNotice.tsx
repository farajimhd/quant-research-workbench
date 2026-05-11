import type { ReactNode } from "react";

type InlineNoticeTone = "danger" | "info" | "success" | "warning";

export function InlineNotice({
  children,
  icon,
  title,
  tone
}: {
  children?: ReactNode;
  icon?: ReactNode;
  title?: ReactNode;
  tone: InlineNoticeTone;
}) {
  return (
    <div className="inline-notice" data-tone={tone}>
      {icon ? <div className="inline-notice-icon">{icon}</div> : null}
      <div className="inline-notice-body">
        {title ? <div className="inline-notice-title">{title}</div> : null}
        {children ? <div className="inline-notice-description">{children}</div> : null}
      </div>
    </div>
  );
}
