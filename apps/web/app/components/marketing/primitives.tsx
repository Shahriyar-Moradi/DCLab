import { buttonClassName } from "@/app/components/ui/Button";
import { cn } from "@/lib/cn";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-eyebrow uppercase tracking-[0.18em] text-brand", className)}>{children}</p>;
}

export function MarketingWrap({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("marketing-wrap", className)}>{children}</div>;
}

export function MarketingSection({
  children,
  className,
  invert = false,
}: {
  children: ReactNode;
  className?: string;
  invert?: boolean;
}) {
  return (
    <section className={cn(invert ? "bg-midnight text-white" : "bg-transparent", className)}>
      <MarketingWrap className="py-16 lg:py-24">{children}</MarketingWrap>
    </section>
  );
}

export function PageHero({
  eyebrow,
  title,
  subtitle,
  invert = false,
  actions,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  invert?: boolean;
  actions?: ReactNode;
}) {
  return (
    <MarketingSection invert={invert}>
      <div className="mx-auto max-w-3xl text-center">
        <Eyebrow className={invert ? "text-cyan" : "text-brand"}>{eyebrow}</Eyebrow>
        <h1
          className={cn(
            "mt-4 text-display tracking-tight",
            invert ? "text-white" : "text-ink",
          )}
        >
          {title}
        </h1>
        <p className={cn("mx-auto mt-4 max-w-2xl text-base leading-7", invert ? "text-white/70" : "text-ink-muted")}>
          {subtitle}
        </p>
        {actions ? <div className="mt-8 flex flex-wrap justify-center gap-3">{actions}</div> : null}
      </div>
    </MarketingSection>
  );
}

export function FeatureCard({
  icon: Icon,
  title,
  body,
  href,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  href?: string;
}) {
  const inner = (
    <>
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-navy-soft text-brand">
        <Icon size={20} strokeWidth={1.75} />
      </div>
      <h3 className="mt-4 text-card text-ink">{title}</h3>
      <p className="mt-2 text-body leading-6 text-ink-muted">{body}</p>
    </>
  );
  const className = "rounded-2xl border border-hairline bg-paper-raised p-6 transition-ui hover:border-navy/25";
  if (href) {
    return (
      <Link href={href} className={cn(className, "block")}>
        {inner}
      </Link>
    );
  }
  return <article className={className}>{inner}</article>;
}

export function MarketingButton({
  href,
  children,
  variant = "primary",
  invert = false,
}: {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary";
  invert?: boolean;
}) {
  if (invert && variant === "secondary") {
    return (
      <Link
        href={href}
        className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-full border border-white/20 px-5 text-button text-white transition-ui hover:bg-white/10 sm:w-auto"
      >
        {children}
      </Link>
    );
  }
  if (invert && variant === "primary") {
    return (
      <Link
        href={href}
        className="marketing-cta-primary inline-flex h-10 w-full items-center justify-center gap-2 rounded-full px-5 text-button text-white transition-ui sm:w-auto"
      >
        {children}
      </Link>
    );
  }
  if (variant === "primary") {
    return (
      <Link
        href={href}
        className="marketing-cta-primary inline-flex h-10 w-full items-center justify-center gap-2 rounded-full px-5 text-button text-white transition-ui sm:w-auto"
      >
        {children}
      </Link>
    );
  }
  return (
    <Link href={href} className={buttonClassName({ variant, size: "lg", className: "w-full rounded-full px-5 sm:w-auto" })}>
      {children}
    </Link>
  );
}

export function MarketingPage({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={className}>{children}</div>;
}
