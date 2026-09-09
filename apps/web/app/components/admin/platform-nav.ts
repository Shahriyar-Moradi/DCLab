import type { AppNavigationSection } from "@/app/components/layout/app-navigation";
import { ADMIN_REGISTRY_HREF } from "@/app/components/admin/paths";
import { Building2, FlaskConical, LineChart, Network, Users } from "lucide-react";

function prefixMatch(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function isPlatformPipelineMonitor(pathname: string) {
  return /^\/admin\/pipeline-runs\/[^/]+\/monitor(?:\/|$)/.test(pathname);
}

export const PLATFORM_NAV_SECTION: AppNavigationSection = {
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
      id: "organizations",
      label: "Organizations",
      href: "/admin/organizations",
      icon: Users,
      audience: "platform",
      isActive: (pathname) => prefixMatch(pathname, "/admin/organizations"),
    },
    {
      id: "admin-labs",
      label: "Labs & Experiments",
      href: "/admin/lab",
      icon: FlaskConical,
      audience: "platform",
      isActive: (pathname) => prefixMatch(pathname, "/admin/lab"),
    },
    {
      id: "registry",
      label: "Model Registry",
      href: ADMIN_REGISTRY_HREF,
      icon: Network,
      audience: "platform",
      isActive: (pathname) => prefixMatch(pathname, ADMIN_REGISTRY_HREF),
    },
    {
      id: "monitoring",
      label: "Monitoring",
      href: "/admin/monitoring",
      icon: LineChart,
      audience: "platform",
      isActive: (pathname) =>
        prefixMatch(pathname, "/admin/monitoring") || isPlatformPipelineMonitor(pathname),
    },
  ],
};
