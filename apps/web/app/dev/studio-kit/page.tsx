import { notFound } from "next/navigation";
import { StudioKit } from "./StudioKit";

export const dynamic = "force-dynamic";
export const metadata = { title: "Studio kit (development)", robots: { index: false, follow: false } };

/**
 * Development-only design kit. The middleware limits /dev/* to the development
 * workspace capability; the page additionally 404s in production builds unless
 * DCLAB_STUDIO_KIT=1. `?theme=light|dark` renders one theme (default: both).
 */
export default async function StudioKitPage({ searchParams }: { searchParams: Promise<{ theme?: string }> }) {
  if (process.env.NODE_ENV === "production" && process.env.DCLAB_STUDIO_KIT !== "1") notFound();
  const { theme } = await searchParams;
  const themes: Array<"light" | "dark"> = theme === "light" || theme === "dark" ? [theme] : ["light", "dark"];
  return <StudioKit themes={themes} />;
}
