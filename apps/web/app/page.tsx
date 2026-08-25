"use client";

import { CaseStudySection, GetStartedCTA, IntegrationsSection, WhyUsSection } from "@/app/components/marketing/sections";
import { ArrowRight, ChevronDown, Sparkles, TrendingUp } from "lucide-react";
import Link from "next/link";

export default function HomePage() {
  return (
    <div>
      <section className="bg-orb relative overflow-hidden">
        <div className="mx-auto grid max-w-7xl items-center gap-12 px-5 py-16 lg:grid-cols-2 lg:px-8 lg:py-24">
          <div>
            <p className="flex items-center gap-2 text-eyebrow uppercase text-brand">
              <Sparkles size={14} strokeWidth={1.75} /> The AI Decision Intelligence Company
            </p>
            <h1 className="mt-4 text-4xl font-bold tracking-tight text-ink lg:text-6xl">
              We Build AI That <span className="text-brand-gradient">Grows Businesses.</span>
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-ink-muted">
              We build autonomous AI systems that continuously improve marketing, sales, pricing, and customer success
              through predictive intelligence and machine learning.
            </p>
            <p className="mt-3 max-w-xl text-base leading-7 text-ink-muted">
              Instead of replacing your team, our AI becomes a decision-making partner that learns from your business
              every day.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/opportunities/upload"
                className="bg-brand-gradient shadow-brand inline-flex items-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-white"
              >
                Book a Demo <ArrowRight size={16} />
              </Link>
              <Link
                href="/platform"
                className="inline-flex items-center rounded-full border border-hairline bg-white px-6 py-3 text-sm font-semibold text-ink"
              >
                See Platform
              </Link>
            </div>
          </div>
          <div className="rounded-3xl bg-white p-6 shadow-xl ring-1 ring-hairline">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-2 font-semibold text-ink">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-white">
                  <Sparkles size={16} strokeWidth={1.75} />
                </span>
                AI Decision Engine
              </p>
              <span className="flex items-center gap-1.5 text-xs font-semibold text-ink-muted">
                <span className="h-2 w-2 rounded-full bg-green" /> Live
              </span>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="rounded-2xl border border-hairline p-4">
                <p className="text-xs text-ink-muted">Marketing Data</p>
                <p className="mt-1 text-xl font-bold text-ink">12,480 signals</p>
              </div>
              <div className="rounded-2xl border border-hairline p-4">
                <p className="text-xs text-ink-muted">Revenue Forecast</p>
                <p className="mt-1 text-xl font-bold text-brand">+$2.4M</p>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-sm">
              <span className="text-ink">Campaign Agent</span>
              <span className="font-semibold text-brand">Analyzing...</span>
            </div>
            <div className="mt-2 flex items-center justify-between text-sm">
              <span className="text-ink">Prediction Model</span>
              <span className="font-semibold text-cyan">96% confidence</span>
            </div>
            <div className="bg-brand-gradient mt-5 flex items-center gap-3 rounded-2xl px-5 py-4 text-white">
              <TrendingUp strokeWidth={1.75} />
              <div>
                <p className="text-lg font-bold">+32% Growth Opportunity</p>
                <p className="text-sm text-white/80">Predicted across 3 channels</p>
              </div>
            </div>
          </div>
        </div>
        <p className="pb-8 text-center text-xs font-semibold uppercase tracking-[0.2em] text-ink-muted">
          Scroll <ChevronDown className="mx-auto mt-1" size={16} />
        </p>
      </section>
      <WhyUsSection />
      <IntegrationsSection />
      <CaseStudySection />
      <GetStartedCTA />
    </div>
  );
}
