import type { AppNavigationSection } from "@/app/components/layout/app-navigation";
import { ADMIN_REGISTRY_HREF } from "@/app/components/admin/paths";
import { Building2, FlaskConical, LineChart, Network } from "lucide-react";

function prefixMatch(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
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
      isActive: (pathname) => prefixMatch(pathname, "/admin/monitoring"),
    },
  ],
};
