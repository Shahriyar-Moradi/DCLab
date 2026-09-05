"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { useSession } from "@/lib/application";
import { defaultProductRoute } from "@/app/components/layout/app-navigation";
import { BOOK_A_DEMO_HREF, MARKETING_NAV } from "@/app/components/marketing/links";
import { buttonClassName } from "@/app/components/ui/Button";
import { useBodyScrollLock, useEscape, useFocusTrap } from "@/app/components/ui/overlay";
import { cn } from "@/lib/cn";
import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";

function isMarketingActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { user, loaded } = useSession();
  const menuId = useId();
  const headerRef = useRef<HTMLElement>(null);
  const sessionHref = loaded && user ? defaultProductRoute(user.role) : "/login";
  const sessionLabel = loaded && user ? "Open workspace" : "Sign In";
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useBodyScrollLock(open);
  useEscape(open, close);
  useFocusTrap(open, headerRef);

  return (
    <header ref={headerRef} className="marketing-header">
      <div className="marketing-wrap flex h-16 items-center justify-between gap-4">
        <BrandLogo />
        <nav className="hidden items-center gap-6 lg:flex" aria-label="Marketing">
          {MARKETING_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-[14px] font-medium transition-ui",
                isMarketingActive(pathname, item.href) ? "text-ink" : "text-ink-muted hover:text-ink",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="hidden items-center gap-4 lg:flex">
          <Link href={sessionHref} className="text-[14px] font-medium text-ink-muted transition-ui hover:text-ink">
            {sessionLabel}
          </Link>
          <Link href={BOOK_A_DEMO_HREF} className={buttonClassName({ size: "md", className: "rounded-full" })}>
            Book a Demo
          </Link>
        </div>
        <button
          type="button"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md text-ink lg:hidden"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls={menuId}
          onClick={() => setOpen((current) => !current)}
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>
      {open ? (
        <div id={menuId} className="border-t border-hairline lg:hidden">
          <nav className="marketing-wrap flex flex-col gap-3 py-4" aria-label="Marketing mobile">
            {MARKETING_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "text-[14px] font-medium",
                  isMarketingActive(pathname, item.href) ? "text-ink" : "text-ink-muted",
                )}
              >
                {item.label}
              </Link>
            ))}
            <Link href={sessionHref} className="text-[14px] font-medium text-ink-muted">
              {sessionLabel}
            </Link>
            <Link href={BOOK_A_DEMO_HREF} className={buttonClassName({ size: "md", className: "mt-1 w-full rounded-full" })}>
              Book a Demo
            </Link>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
