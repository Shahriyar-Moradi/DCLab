import { safeInternalHref } from "./safe-href";

export type Crumb = { label: string; href?: string };

export function Crumbs({ items, label = "Breadcrumb" }: { items: Crumb[]; label?: string }) {
  return (
    <nav className="crumbs" aria-label={label}>
      <ol>
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`}>
              {safeInternalHref(item.href) && !last ? <a href={safeInternalHref(item.href) ?? undefined}>{item.label}</a> : <span aria-current={last ? "page" : undefined}>{item.label}</span>}
              {last ? null : <span aria-hidden="true"> /</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
