"use client";

import { PLATFORM_NAV_SECTION } from "@/app/components/admin/platform-nav";
import {
  BarChart3,
  ClipboardList,
  Code2,
  FlaskConical,
  type LucideIcon,
  LayoutDashboard,
  Lightbulb,
  Scale,
  Upload,
} from "lucide-react";
import { CAPABILITIES, defaultProductRoute, hasCapability } from "@/lib/infrastructure/capabilities";
import type { SessionUser } from "@/lib/infrastructure/session";

type NavAudience = "all" | "platform" | "business" | "development";

export type AppNavigationItem = {
  id: string;
  label: string;
  href: string;
  icon: LucideIcon;
  audience: NavAudience;
  isActive: (pathname: string) => boolean;
};

export type AppNavigationSection = {
  id: string;
  label: string;
  audience: NavAudience;
  items: AppNavigationItem[];
};

const prefixMatch = (pathname: string, href: string) =>
  pathname === href || pathname.startsWith(`${href}/`);

export const APP_NAVIGATION: AppNavigationSection[] = [
  {
    id: "workspace",
    label: "Workspace",
    audience: "all",
    items: [
      {
        id: "dashboard",
        label: "Dashboard",
        href: "/app/dashboards",
        icon: LayoutDashboard,
        audience: "all",
        isActive: (pathname) => prefixMatch(pathname, "/app/dashboards"),
      },
      {
        id: "insights",
        label: "Insights",
        href: "/app/insights",
        icon: Lightbulb,
        audience: "all",
        isActive: (pathname) => prefixMatch(pathname, "/app/insights"),
      },
      {
        id: "opportunities",
        label: "Opportunities",
        href: "/app/opportunities",
        icon: BarChart3,
        audience: "all",
        isActive: (pathname) =>
          prefixMatch(pathname, "/app/opportunities") &&
          !pathname.startsWith("/app/opportunities/upload"),
      },
      {
        id: "decisions",
        label: "Decisions",
        href: "/app/decisions",
        icon: ClipboardList,
        audience: "all",
        isActive: (pathname) => prefixMatch(pathname, "/app/decisions"),
      },
    ],
  },
  {
    id: "ml-workspace",
    label: "ML Workspace",
    audience: "all",
    items: [
      {
        id: "upload",
        label: "Upload",
        href: "/app/opportunities/upload",
        icon: Upload,
        audience: "all",
        isActive: (pathname) => prefixMatch(pathname, "/app/opportunities/upload"),
      },
      {
        id: "labs",
        label: "Labs",
        href: "/app/labs",
        icon: FlaskConical,
        audience: "all",
        isActive: (pathname) =>
          prefixMatch(pathname, "/app/labs") || pathname.startsWith("/lab/runs"),
      },
    ],
  },
  PLATFORM_NAV_SECTION,
  {
    id: "business",
    label: "Business",
    audience: "business",
    items: [
      {
        id: "business-admin",
        label: "Business Admin",
        href: "/business",
        icon: Scale,
        audience: "business",
        isActive: (pathname) => prefixMatch(pathname, "/business"),
      },
    ],
  },
  {
    id: "development",
    label: "Development",
    audience: "development",
    items: [
      {
        id: "development-home",
        label: "Development",
        href: "/development",
        icon: Code2,
        audience: "development",
        isActive: (pathname) => prefixMatch(pathname, "/development"),
      },
    ],
  },
];

function isVisible(audience: NavAudience, user: SessionUser) {
  if (audience === "all") return hasCapability(user, CAPABILITIES.applicationAccess);
  if (audience === "platform") return hasCapability(user, CAPABILITIES.platformRead);
  if (audience === "development") return hasCapability(user, CAPABILITIES.developmentAccess);
  return hasCapability(user, CAPABILITIES.businessAccess);
}

export function navigationForUser(user: SessionUser | null) {
  if (!user) return [];
  return APP_NAVIGATION.filter((section) => isVisible(section.audience, user))
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => isVisible(item.audience, user)),
    }))
    .filter((section) => section.items.length > 0);
}

export type CommandDestination = {
  href: string;
  label: string;
  group: string;
};

export function commandDestinationsForUser(user: SessionUser | null): CommandDestination[] {
  const destinations: CommandDestination[] = navigationForUser(user).flatMap((section) =>
    section.items.map((item) => ({ href: item.href, label: item.label, group: section.label })),
  );
  if (hasCapability(user, CAPABILITIES.accountAccess)) {
    destinations.push({ href: "/app/settings", label: "Account", group: "Workspace" });
  }
  if (hasCapability(user, CAPABILITIES.platformRead)) {
    destinations.push({ href: "/admin/organizations", label: "Organizations", group: "Platform" });
  }
  const seen = new Set<string>();
  return destinations.filter((item) => {
    if (seen.has(item.href)) return false;
    seen.add(item.href);
    return true;
  });
}

export function activeNavigationItem(pathname: string, user: SessionUser | null) {
  return navigationForUser(user)
    .flatMap((section) => section.items)
    .filter((item) => item.isActive(pathname))
    .sort((left, right) => right.href.length - left.href.length)[0];
}

export { defaultProductRoute };

export function isProductRoute(pathname: string) {
  return ["/app", "/lab", "/admin", "/business", "/development"].some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/** Studio design kit: renders its own shell, outside marketing and product chrome. */
export function isStudioRoute(pathname: string) {
  return pathname === "/dev" || pathname.startsWith("/dev/");
}

export function isAuthRoute(pathname: string) {
  return pathname === "/login" || pathname.startsWith("/login/");
}
