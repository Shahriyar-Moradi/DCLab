"use client";

import {
  BarChart3,
  Building2,
  ClipboardList,
  FlaskConical,
  type LucideIcon,
  LayoutDashboard,
  Lightbulb,
  LineChart,
  Network,
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
  {
    id: "platform",
    label: "Platform",
    audience: "platform",
    items: [
      {
        id: "businesses",
        label: "Businesses",
        href: "/admin/businesses",
        icon: Building2,
        audience: "platform",
        isActive: (pathname) => prefixMatch(pathname, "/admin/businesses"),
      },
      {
        id: "admin-labs",
        label: "Labs & Experiments",
        href: "/admin/lab",
        icon: FlaskConical,
        audience: "platform",
        isActive: (pathname) =>
          prefixMatch(pathname, "/admin/lab") || pathname.startsWith("/admin/pipeline-runs"),
      },
      {
        id: "registry",
        label: "Model Registry",
        href: "/admin/models",
        icon: Network,
        audience: "platform",
        isActive: (pathname) => prefixMatch(pathname, "/admin/models"),
      },
      {
        id: "monitoring",
        label: "Monitoring",
        href: "/admin/monitoring",
        icon: LineChart,
        audience: "platform",
        isActive: (pathname) => prefixMatch(pathname, "/admin/monitoring"),
      },
    ],
  },
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
