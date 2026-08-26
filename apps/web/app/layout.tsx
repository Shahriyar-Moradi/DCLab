import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Public_Sans } from "next/font/google";
import { SiteFooter } from "@/app/components/layout/SiteFooter";
import { SiteHeader } from "@/app/components/layout/SiteHeader";
import { SiteMain } from "@/app/components/layout/SiteMain";
import { QueryProvider } from "@/lib/application";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["500", "600"],
  variable: "--font-display",
  display: "swap",
});

const publicSans = Public_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Decision.ai — Decision Intelligence",
  description:
    "Decision.ai scores every opportunity, recommends the next action, and runs reproducible experiments in the Experimentation Lab.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${fraunces.variable} ${publicSans.variable} ${ibmPlexMono.variable} bg-paper font-body text-ink antialiased`}>
        <QueryProvider>
          <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-navy focus:px-3 focus:py-2 focus:text-white">
            Skip to content
          </a>
          <SiteHeader />
          <SiteMain>{children}</SiteMain>
          <SiteFooter />
        </QueryProvider>
      </body>
    </html>
  );
}
