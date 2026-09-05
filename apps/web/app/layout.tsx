import type { Metadata } from "next";
import localFont from "next/font/local";
import { RouteShell } from "@/app/components/layout/RouteShell";
import { QueryProvider } from "@/lib/application";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-sans",
  weight: "100 900",
  display: "swap",
});

const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-mono",
  weight: "100 900",
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
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="bg-paper font-sans text-ink antialiased">
        <QueryProvider>
          <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-navy focus:px-3 focus:py-2 focus:text-white">
            Skip to content
          </a>
          <RouteShell>{children}</RouteShell>
        </QueryProvider>
      </body>
    </html>
  );
}
