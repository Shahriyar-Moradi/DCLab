import type { ReactNode } from "react";

export type PageHeadProps = {
  title: string;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  /** Ref/status badges rendered under the title. */
  badges?: ReactNode;
  actions?: ReactNode;
};

export function PageHead({ title, eyebrow, subtitle, badges, actions }: PageHeadProps) {
  return (
    <div className="page-head">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {badges ? <div className="toolbar">{badges}</div> : null}
        {subtitle ? <p className="sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="toolbar">{actions}</div> : null}
    </div>
  );
}
