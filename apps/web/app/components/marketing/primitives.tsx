import { cn } from "@/lib/cn";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function Eyebrow({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p className={cn("text-eyebrow uppercase text-brand", className)}>{children}</p>
  );
}

export function PageHero({
  eyebrow,
  title,
  subtitle,
  invert = false,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  invert?: boolean;
}) {
  return (
    <div className="mx-auto max-w-3xl px-5 pt-16 text-center lg:px-8 lg:pt-20">
      <Eyebrow className={invert ? "text-cyan" : "text-brand"}>{eyebrow}</Eyebrow>
      <h1 className={cn("mt-4 text-4xl font-bold tracking-tight lg:text-5xl", invert ? "text-white" : "text-ink")}>
        {title}
      </h1>
      <p className={cn("mx-auto mt-4 max-w-2xl text-base leading-7", invert ? "text-white/70" : "text-ink-muted")}>
        {subtitle}
      </p>
    </div>
  );
}

export function FeatureCard({
  icon: Icon,
  title,
  body,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
}) {
  return (
    <article className="rounded-2xl bg-[#F4F7FB] p-6">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-brand shadow-sm">
        <Icon size={20} strokeWidth={1.75} />
      </div>
      <h3 className="mt-4 text-base font-semibold text-ink">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-ink-muted">{body}</p>
    </article>
  );
}

export function MarketingShell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("pb-20", className)}>{children}</div>;
}
