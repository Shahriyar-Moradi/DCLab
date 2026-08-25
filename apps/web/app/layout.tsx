import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Public_Sans } from "next/font/google";
import { AppShell } from "@/app/components/layout/AppShell";
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
  title: "Decision.ai",
  description: "Internal decision layer — scored opportunities, recommended actions, and why.",
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
          <AppShell>{children}</AppShell>
        </QueryProvider>
      </body>
    </html>
  );
}
