import {
  ArrowRight,
  BarChart3,
  Beaker,
  Building2,
  ClipboardList,
  LayoutDashboard,
  Lightbulb,
  LineChart,
  Megaphone,
  Plug,
  RefreshCw,
  Rocket,
  ScanSearch,
  Target,
  Upload,
  UserCheck,
} from "lucide-react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { BOOK_A_DEMO_HREF } from "./links";
import { Eyebrow, FeatureCard, MarketingButton, MarketingSection, MarketingWrap } from "./primitives";

export const ML_FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: Target, title: "Conversion scoring", body: "Score which opportunities are more likely to convert from the fields you upload." },
  { icon: UserCheck, title: "Lead ranking", body: "Rank pipeline rows by the recorded recommendation and confidence band." },
  { icon: LineChart, title: "Revenue views", body: "Read expected value from decisions currently in the ledger — not a separate forecast product." },
  { icon: ScanSearch, title: "Recommended actions", body: "Each decision returns a next action with reasoning the workspace can audit." },
  { icon: Beaker, title: "Lab experiments", body: "Profile a dataset, define a task, and run a budgeted candidate search against a baseline." },
  { icon: RefreshCw, title: "Retrain history", body: "Staff monitoring shows consecutive evaluation deltas when a later run exists." },
  { icon: Lightbulb, title: "Translated insights", body: "Client insights are generated from the latest recorded simulation or trial for a use case." },
  { icon: ClipboardList, title: "Decision ledger", body: "Keep an append-only record of scored opportunities and the action that was recommended." },
];

export const SERVICES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: ScanSearch, title: "Decision layer design", body: "Map opportunity fields onto scoring, recommended actions, and an auditable ledger." },
  { icon: Beaker, title: "Experimentation Lab", body: "Stand up dataset profiling, task specs, and candidate search without a new CRM." },
  { icon: LineChart, title: "Predictive workflows", body: "Connect conversion, ranking, and holdout evaluation to the models the lab actually trains." },
  { icon: Plug, title: "Workspace onboarding", body: "Load historical opportunities from CSV and review decisions in a role-aware workspace." },
  { icon: Rocket, title: "Rollout support", body: "Adopt the operating surfaces that already exist: dashboards, insights, labs, and administration." },
];

export const PLATFORM_SURFACES: { icon: LucideIcon; label: string; href: string; body: string }[] = [
  { icon: LayoutDashboard, label: "Dashboard", href: "/app/dashboards", body: "Workspace totals from opportunities and the decision ledger." },
  { icon: BarChart3, label: "Opportunities", href: "/app/opportunities", body: "The pipeline rows the engine scores." },
  { icon: ClipboardList, label: "Decisions", href: "/app/decisions", body: "Recommended actions, confidence, and expected value." },
  { icon: Lightbulb, label: "Insights", href: "/app/insights", body: "Translated findings from recorded trials." },
  { icon: Beaker, label: "Labs", href: "/app/labs", body: "Bounded problem trials and dataset uploads." },
  { icon: Building2, label: "Administration", href: "/business", body: "Tenant explorer for authorized business roles." },
];

export const INDUSTRIES = [
  { icon: Building2, title: "Financial services", body: "Score pipeline quality, prioritize outreach, and audit every recommended action." },
  { icon: LineChart, title: "B2B SaaS", body: "Turn product and CRM exports into conversion scores and next-best actions." },
  { icon: Megaphone, title: "Marketing and growth", body: "Connect campaign files to opportunity scoring without replacing your stack." },
  { icon: Rocket, title: "Enterprise sales", body: "A decision layer on top of the CRM export — generate, review, and explain actions." },
];

export function WhyUsSection() {
  return (
    <MarketingSection>
      <Eyebrow className="text-center">Why this product exists</Eyebrow>
      <h2 className="mt-4 text-center text-title text-ink lg:text-[2rem]">A decision layer that stays in the loop</h2>
      <div className="mt-12 grid gap-6 lg:grid-cols-2">
        <article className="rounded-2xl border border-hairline bg-paper-raised p-8">
          <p className="text-eyebrow uppercase tracking-[0.18em] text-ink-muted">One-off consulting</p>
          <h3 className="mt-2 text-section text-ink">A report, then a pause</h3>
          <ol className="mt-8 space-y-3 text-body text-ink-muted">
            <li>Static slides</li>
            <li>A round of meetings</li>
            <li>Recommendations that expire</li>
          </ol>
        </article>
        <article className="rounded-2xl bg-midnight p-8 text-white">
          <p className="text-eyebrow uppercase tracking-[0.18em] text-cyan">This workspace</p>
          <h3 className="mt-2 text-section">Score, decide, re-run</h3>
          <ol className="mt-8 space-y-3 text-body text-white/80">
            <li>Upload opportunities</li>
            <li>Record an audited decision</li>
            <li>Compare models in the Lab</li>
          </ol>
        </article>
      </div>
    </MarketingSection>
  );
}

export function ProductPathSection() {
  return (
    <MarketingSection>
      <Eyebrow className="text-center">How it works</Eyebrow>
      <h2 className="mt-4 text-center text-title text-ink lg:text-[2rem]">From upload to decision to experiment</h2>
      <div className="mt-12 grid gap-6 lg:grid-cols-3">
        <FeatureCard
          icon={Upload}
          title="Upload opportunities"
          body="Drop in a CSV of historical sales opportunities — external ID, amount, stage, source, owner."
          href="/app/opportunities/upload"
        />
        <FeatureCard
          icon={ScanSearch}
          title="Score and decide"
          body="The decision engine scores each row and returns a recommended action with confidence and reasoning."
          href="/app/decisions"
        />
        <FeatureCard
          icon={Beaker}
          title="Experiment in Labs"
          body="Profile a dataset, define a prediction task, and run a budgeted candidate search against a baseline."
          href="/app/labs"
        />
      </div>
    </MarketingSection>
  );
}

export function SurfaceGrid() {
  return (
    <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {PLATFORM_SURFACES.map((item) => (
        <FeatureCard key={item.label} icon={item.icon} title={item.label} body={item.body} href={item.href} />
      ))}
    </div>
  );
}

export function MLGrid() {
  return (
    <MarketingWrap className="grid gap-4 pb-4 sm:grid-cols-2 lg:grid-cols-4">
      {ML_FEATURES.map((item) => (
        <FeatureCard key={item.title} {...item} />
      ))}
    </MarketingWrap>
  );
}

export function ServicesGrid() {
  return (
    <MarketingWrap className="grid gap-4 pb-4 md:grid-cols-2 lg:grid-cols-3">
      {SERVICES.map((item) => (
        <FeatureCard key={item.title} {...item} />
      ))}
      <article className="flex flex-col justify-between rounded-2xl bg-midnight p-6 text-white">
        <div>
          <h3 className="text-card">Not sure where to start?</h3>
          <p className="mt-2 text-body text-white/75">Book a walkthrough of the workspace that already exists.</p>
        </div>
        <Link
          href={BOOK_A_DEMO_HREF}
          className="mt-6 inline-flex h-10 w-fit items-center rounded-full bg-white px-5 text-button font-medium text-navy"
        >
          Book a Demo
        </Link>
      </article>
    </MarketingWrap>
  );
}

export function DataInSection() {
  return (
    <MarketingWrap className="grid gap-6 pb-8 md:grid-cols-2">
      <FeatureCard
        icon={Upload}
        title="Opportunity CSV"
        body="Historical sales rows become the workspace ledger. Sign in to upload."
        href="/app/opportunities/upload"
      />
      <FeatureCard
        icon={Beaker}
        title="Labs datasets"
        body="Spreadsheet and table uploads drive bounded trials and auto-train jobs."
        href="/app/labs"
      />
    </MarketingWrap>
  );
}

export function GetStartedCTA() {
  return (
    <MarketingSection invert>
      <div className="text-center">
        <Eyebrow className="text-cyan">Get started</Eyebrow>
        <h2 className="mx-auto mt-4 max-w-3xl text-title text-white lg:text-[2rem]">
          Open the workspace, or talk with the team
        </h2>
        <p className="mx-auto mt-4 max-w-2xl text-body text-white/70">
          Decision.ai scores opportunities, recommends the next action, and keeps experiments in the Lab.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <MarketingButton href={BOOK_A_DEMO_HREF} invert>
            Book a Demo <ArrowRight size={16} />
          </MarketingButton>
          <MarketingButton href="/platform" variant="secondary" invert>
            Explore the Platform
          </MarketingButton>
        </div>
      </div>
    </MarketingSection>
  );
}

export function PricingPanel() {
  return (
    <MarketingWrap className="grid gap-6 pb-8 lg:grid-cols-2">
      <article className="rounded-2xl border border-hairline bg-paper-raised p-8">
        <h2 className="text-section text-ink">Workspace</h2>
        <p className="mt-3 text-body text-ink-muted">
          Dashboards, opportunities, decisions, insights, and client Labs. Sign in if you already have an account.
        </p>
        <div className="mt-8">
          <MarketingButton href="/login">Sign In</MarketingButton>
        </div>
      </article>
      <article className="rounded-2xl bg-midnight p-8 text-white">
        <h2 className="text-section">Commercial terms</h2>
        <p className="mt-3 text-body text-white/70">
          This product does not publish self-serve plan prices or packaged agent seats. Discuss access with the team.
        </p>
        <div className="mt-8">
          <MarketingButton href={BOOK_A_DEMO_HREF} invert>
            Book a Demo
          </MarketingButton>
        </div>
      </article>
    </MarketingWrap>
  );
}
