import type { ReactNode } from "react";

type PageIntroProps = {
  className?: string;
  groupLabel: string;
  title: string;
  description: string;
  actions?: ReactNode;
};

export function PageIntro({ className, groupLabel, title, description, actions }: PageIntroProps) {
  return (
    <div className={className ? `page-intro ${className}` : "page-intro"}>
      <div className="page-intro-copy">
        <div className="page-kicker">{groupLabel}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-intro-actions">{actions}</div> : null}
    </div>
  );
}
