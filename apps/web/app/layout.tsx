import type { Metadata } from "next";
import localFont from "next/font/local";
import { RouteShell } from "@/app/components/layout/RouteShell";
import { QueryProvider } from "@/lib/application";
import "./globals.css";

// D1 (founder decision 7): the v7 demo fonts, committed under app/fonts (Latin subsets, SIL OFL 1.1: see
// app/fonts/OFL.txt) and served from this app, so builds work offline and the browser never calls Google.
// Inter is the body face everywhere (--font-sans, so the marketing and sign-in pages stay consistent), JetBrains
// Mono the code face (--font-mono), Manrope the Studio heading face (--font-manrope, used by components/studio/studio.css).
const inter = localFont({ src: "./fonts/Inter-latin-var.woff2", weight: "100 900", variable: "--font-sans", display: "swap" });
const jetbrainsMono = localFont({ src: "./fonts/JetBrainsMono-latin-var.woff2", weight: "100 800", variable: "--font-mono", display: "swap" });
const manrope = localFont({ src: "./fonts/Manrope-latin-var.woff2", weight: "200 800", variable: "--font-manrope", display: "swap" });

export const metadata: Metadata = {
  title: "DCLab — Decision Intelligence",
  description:
    "DCLab scores every opportunity, recommends the next action, and runs reproducible experiments in the Experimentation Lab.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} ${manrope.variable}`} suppressHydrationWarning>
      <body className="bg-paper font-sans text-ink antialiased" suppressHydrationWarning>
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
