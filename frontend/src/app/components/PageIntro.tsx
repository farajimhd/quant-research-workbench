import type { ReactNode } from "react";

type PageIntroProps = {
  groupLabel: string;
  title: string;
  description: string;
  actions?: ReactNode;
};

export function PageIntro({ groupLabel, title, description, actions }: PageIntroProps) {
  return (
    <div className="page-intro">
      <div className="page-intro-copy">
        <div className="page-kicker">{groupLabel}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-intro-actions">{actions}</div> : null}
    </div>
  );
}

