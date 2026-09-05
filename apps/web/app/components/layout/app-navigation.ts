"use client";

import { PLATFORM_NAV_SECTION } from "@/app/components/admin/platform-nav";
import {
  BarChart3,
  ClipboardList,
  FlaskConical,
  type LucideIcon,
  LayoutDashboard,
  Lightbulb,
  Scale,
  Upload,
} from "lucide-react";
import { isBusinessAdministrationRole, isPlatformRole, type SessionUser } from "@/lib/infrastructure/session";

type NavAudience = "all" | "platform" | "business";

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

const isBusinessRole = (role: SessionUser["role"]) => isBusinessAdministrationRole(role);

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
];

function isVisible(audience: NavAudience, role: SessionUser["role"]) {
  if (audience === "all") return true;
  if (audience === "platform") return isPlatformRole(role);
  return isBusinessRole(role);
}

export function navigationForRole(user: SessionUser | null) {
  if (!user) return [];
  return APP_NAVIGATION.filter((section) => isVisible(section.audience, user.role))
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => isVisible(item.audience, user.role)),
    }))
    .filter((section) => section.items.length > 0);
}

export type CommandDestination = {
  href: string;
  label: string;
  group: string;
};

export function commandDestinationsForRole(user: SessionUser | null): CommandDestination[] {
  const destinations: CommandDestination[] = navigationForRole(user).flatMap((section) =>
    section.items.map((item) => ({ href: item.href, label: item.label, group: section.label })),
  );
  destinations.push({ href: "/app/settings", label: "Account", group: "Workspace" });
  if (user && isPlatformRole(user.role)) {
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
  return navigationForRole(user)
    .flatMap((section) => section.items)
    .filter((item) => item.isActive(pathname))
    .sort((left, right) => right.href.length - left.href.length)[0];
}

export function defaultProductRoute(role: SessionUser["role"]) {
  if (isPlatformRole(role)) return "/admin/businesses";
  if (isBusinessRole(role)) return "/business";
  return "/app/dashboards";
}

export function isProductRoute(pathname: string) {
  return ["/app", "/lab", "/admin", "/business"].some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function isAuthRoute(pathname: string) {
  return pathname === "/login" || pathname.startsWith("/login/");
}
