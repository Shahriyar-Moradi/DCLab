import {
  ArrowDown,
  ArrowRight,
  BarChart3,
  Bot,
  Brain,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  CircleDollarSign,
  Clock,
  Eye,
  FileText,
  Heart,
  Hexagon,
  Lightbulb,
  LineChart,
  Map,
  Megaphone,
  Plug,
  RefreshCw,
  Rocket,
  Search,
  Sparkles,
  Target,
  TrendingUp,
  User,
  UserCheck,
  Zap,
} from "lucide-react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { FeatureCard } from "./primitives";

const BOOK_A_DEMO_HREF = "mailto:hello@decision.ai?subject=Book%20a%20demo";

export const ML_FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: TrendingUp, title: "Revenue Forecasting", body: "Predict future revenue with confidence intervals and scenario modeling." },
  { icon: Target, title: "Conversion Prediction", body: "Forecast which prospects will convert before they even enter your pipeline." },
  { icon: UserCheck, title: "Lead Scoring", body: "Automatically rank every lead by likelihood to close and potential value." },
  { icon: CircleDollarSign, title: "Demand Forecast", body: "Anticipate market demand shifts weeks before they impact your business." },
  { icon: Heart, title: "Customer Lifetime Value", body: "Calculate and predict the true lifetime value of every customer." },
  { icon: RefreshCw, title: "Model Retraining", body: "Models continuously retrain on new data so predictions stay accurate." },
  { icon: Hexagon, title: "Feature Engineering", body: "Automatically discover the data signals that drive the best outcomes." },
  { icon: Eye, title: "Explainable AI", body: "Every prediction comes with a clear explanation of why the AI decided." },
];

export const SERVICES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: Map, title: "AI Strategy", body: "Business AI roadmap — identify where AI creates the most value across your organization." },
  { icon: Bot, title: "AI Agent Development", body: "Custom autonomous agents built for your specific workflows and data sources." },
  { icon: LineChart, title: "Predictive Analytics", body: "Machine learning models tailored to your unique business outcomes and KPIs." },
  { icon: Plug, title: "Enterprise Integration", body: "CRM, ERP, marketing stack, and cloud — connected into one intelligent system." },
  { icon: Rocket, title: "AI Transformation", body: "End-to-end implementation — from strategy to deployed AI across your entire business." },
];

export const AGENTS: {
  icon: LucideIcon;
  name: string;
  confidence: string;
  tasks: string;
  recommendation: string;
  gauge: number;
}[] = [
  { icon: Search, name: "Research Agent", confidence: "94%", tasks: "8,210", recommendation: "Monitor competitor pricing trends in Q3.", gauge: 94 },
  { icon: Megaphone, name: "Marketing Agent", confidence: "96%", tasks: "12,430", recommendation: "Increase Meta Ads budget by 18%.", gauge: 96 },
  { icon: User, name: "Sales Agent", confidence: "91%", tasks: "6,847", recommendation: "Prioritize 14 high-probability leads today.", gauge: 91 },
  { icon: CircleDollarSign, name: "Pricing Agent", confidence: "89%", tasks: "4,512", recommendation: "Adjust premium tier +7% for Q3.", gauge: 89 },
  { icon: Heart, name: "Customer Agent", confidence: "93%", tasks: "9,180", recommendation: "3 accounts at risk — trigger retention flow.", gauge: 93 },
  { icon: BarChart3, name: "Executive Agent", confidence: "97%", tasks: "15,602", recommendation: "Revenue forecast up 8.2% — align hiring plan.", gauge: 97 },
];

export const INTEGRATIONS = [
  { letter: "G", name: "Google Analytics" },
  { letter: "M", name: "Meta Ads" },
  { letter: "H", name: "HubSpot" },
  { letter: "S", name: "Salesforce" },
  { letter: "G", name: "GA4" },
  { letter: "S", name: "Stripe" },
  { letter: "S", name: "Snowflake" },
  { letter: "B", name: "BigQuery" },
  { letter: "S", name: "SQL Server" },
  { letter: "C", name: "CRM Sync" },
  { letter: "E", name: "ERP Connect" },
  { letter: "Z", name: "Zapier" },
];

export const PLATFORM_PILLS: { icon: LucideIcon; label: string; href: string }[] = [
  { icon: BarChart3, label: "Dashboard", href: "/app/dashboards" },
  { icon: LineChart, label: "Marketing", href: "/solutions" },
  { icon: User, label: "Sales", href: "/app/opportunities" },
  { icon: CircleDollarSign, label: "Pricing", href: "/pricing" },
  { icon: Heart, label: "Customer", href: "/solutions" },
  { icon: Hexagon, label: "Machine Learning", href: "/solutions" },
  { icon: Bot, label: "Agents", href: "/platform" },
  { icon: FileText, label: "Reports", href: "/resources" },
  { icon: Sparkles, label: "Lab", href: "/admin/lab" },
];

export const INDUSTRIES = [
  { icon: Building2, title: "Financial Services", body: "Score pipeline quality, prioritize outreach, and audit every recommended action." },
  { icon: LineChart, title: "B2B SaaS", body: "Turn product and CRM signals into conversion scores and next-best actions." },
  { icon: Megaphone, title: "Marketing & Growth", body: "Connect campaign data to opportunity scoring without replacing your stack." },
  { icon: Rocket, title: "Enterprise Sales", body: "A decision layer on top of the CRM — generate, review, and explain actions." },
];

export function WhyUsSection() {
  return (
    <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
      <p className="text-center text-eyebrow uppercase text-brand">Why companies choose us</p>
      <h2 className="mt-4 text-center text-3xl font-bold text-ink lg:text-4xl">
        Continuous Intelligence, Not One-Time Consulting
      </h2>
      <div className="mt-12 grid gap-6 lg:grid-cols-2">
        <article className="rounded-3xl border border-hairline bg-paper-raised p-8 shadow-sm">
          <p className="text-eyebrow uppercase text-ink-muted">Traditional consulting</p>
          <h3 className="mt-2 text-2xl font-bold text-ink">The old way</h3>
          <ol className="mt-8 space-y-4">
            {[
              { icon: FileText, label: "Reports" },
              { icon: Calendar, label: "Meetings" },
              { icon: Lightbulb, label: "Recommendations" },
              { icon: CheckCircle2, label: "Done." },
            ].map((row, index, all) => (
              <li key={row.label} className="flex items-center justify-between">
                <span className="flex items-center gap-3 text-ink">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-navy-soft text-ink-muted">
                    <row.icon size={18} />
                  </span>
                  {row.label}
                </span>
                {index < all.length - 1 ? <ArrowDown size={16} className="text-ink-muted" /> : null}
              </li>
            ))}
          </ol>
          <p className="mt-8 text-sm text-ink-muted">Static. Expires. Then you hire another consultant.</p>
        </article>
        <article className="rounded-3xl bg-midnight p-8 text-white shadow-brand">
          <p className="text-eyebrow uppercase text-cyan">Our platform</p>
          <h3 className="mt-2 text-2xl font-bold">The Decision.ai way</h3>
          <ol className="mt-8 space-y-4">
            {[
              { icon: Brain, label: "AI Learns" },
              { icon: LineChart, label: "Predicts" },
              { icon: Zap, label: "Improves" },
              { icon: RefreshCw, label: "Learns Again" },
            ].map((row) => (
              <li key={row.label} className="flex items-center justify-between">
                <span className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white/10 text-cyan">
                    <row.icon size={18} />
                  </span>
                  {row.label}
                </span>
                <ArrowDown size={16} className="text-cyan" />
              </li>
            ))}
          </ol>
          <p className="mt-8 text-sm text-white/80">Continuous intelligence. It never stops improving.</p>
        </article>
      </div>
    </section>
  );
}

export function IntegrationsSection() {
  return (
    <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
      <h2 className="text-center text-3xl font-bold text-ink lg:text-4xl">Connects to Your Entire Stack</h2>
      <p className="mx-auto mt-4 max-w-2xl text-center text-ink-muted">
        Your data lives everywhere. Decision.ai brings it together into one intelligent system.
      </p>
      <div className="mt-12 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {INTEGRATIONS.map((item) => (
          <div
            key={item.name}
            className="flex items-center gap-3 rounded-2xl bg-paper-raised px-4 py-4 shadow-sm ring-1 ring-hairline transition hover:shadow-md hover:ring-navy/20"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-navy-soft text-sm font-bold text-brand">
              {item.letter}
            </span>
            <span className="text-sm font-medium text-ink">{item.name}</span>
          </div>
        ))}
      </div>
      <p className="mt-8 text-center text-sm text-ink-muted">
        Plus 50+ additional connectors. Don&apos;t see yours?{" "}
        <Link href={BOOK_A_DEMO_HREF} className="font-semibold text-brand">
          Talk to us →
        </Link>
      </p>
    </section>
  );
}

export function CaseStudySection() {
  return (
    <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
      <p className="text-center text-eyebrow uppercase text-brand">Case studies</p>
      <h2 className="mt-4 text-center text-3xl font-bold text-ink lg:text-4xl">Proof, Not Promises</h2>
      <div className="mt-12 overflow-hidden rounded-3xl shadow-sm ring-1 ring-hairline lg:grid lg:grid-cols-2">
        <div className="bg-paper-raised p-8 lg:p-10">
          <div className="flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-midnight text-white">
              <Building2 size={22} />
            </span>
            <div>
              <p className="font-bold text-ink">Multibank</p>
              <p className="text-sm text-ink-muted">AI Marketing Intelligence</p>
            </div>
          </div>
          <p className="mt-8 text-eyebrow uppercase text-ink-muted">Problem</p>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            Manual reporting and slow decision-making across 12 markets left teams reacting days after the window closed.
          </p>
          <p className="mt-6 text-eyebrow uppercase text-ink-muted">Solution</p>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            Decision.ai scores opportunities, recommends the next action, and keeps an audit trail so every market can
            move on the same intelligence.
          </p>
        </div>
        <div className="bg-midnight p-8 text-white lg:p-10">
          <p className="text-eyebrow uppercase text-cyan">Results</p>
          <div className="mt-6 grid grid-cols-2 gap-6">
            {[
              { icon: Target, value: "+41%", label: "Lead Quality" },
              { icon: TrendingUp, value: "+22%", label: "Conversion" },
              { icon: CircleDollarSign, value: "+35%", label: "Campaign ROI" },
              { icon: Clock, value: "-90%", label: "Reporting Time" },
            ].map((stat) => (
              <div key={stat.label}>
                <stat.icon size={18} className="text-cyan" />
                <p className="mt-2 text-3xl font-bold text-cyan">{stat.value}</p>
                <p className="text-sm text-white/70">{stat.label}</p>
              </div>
            ))}
          </div>
          <Link href="/resources" className="mt-8 inline-flex items-center gap-2 text-sm font-semibold text-cyan">
            Read full case study <ArrowRight size={16} />
          </Link>
        </div>
      </div>
    </section>
  );
}

export function MLGrid() {
  return (
    <div className="mx-auto mt-12 grid max-w-7xl gap-4 px-5 sm:grid-cols-2 lg:grid-cols-4 lg:px-8">
      {ML_FEATURES.map((item) => (
        <FeatureCard key={item.title} {...item} />
      ))}
    </div>
  );
}

export function ServicesGrid() {
  return (
    <div className="mx-auto mt-12 grid max-w-7xl gap-4 px-5 md:grid-cols-2 lg:grid-cols-3 lg:px-8">
      {SERVICES.map((item) => (
        <FeatureCard key={item.title} {...item} />
      ))}
      <article className="bg-brand-gradient flex flex-col justify-between rounded-2xl p-6 text-white shadow-brand">
        <div>
          <h3 className="text-xl font-bold">Not sure where to start?</h3>
          <p className="mt-2 text-sm text-white/85">Book a free consultation and start your AI transformation journey.</p>
        </div>
        <Link
          href={BOOK_A_DEMO_HREF}
          className="mt-6 inline-flex w-fit items-center rounded-full bg-white px-5 py-2.5 text-sm font-semibold text-brand"
        >
          Book a Consultation
        </Link>
      </article>
    </div>
  );
}

export function AgentsGrid() {
  return (
    <div className="mx-auto mt-12 grid max-w-7xl gap-4 px-5 md:grid-cols-2 lg:grid-cols-3 lg:px-8">
      {AGENTS.map((agent) => (
        <article key={agent.name} className="rounded-2xl bg-white/5 p-5 ring-1 ring-white/10">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan/15 text-cyan">
                <agent.icon size={18} />
              </span>
              <div>
                <p className="font-semibold text-white">{agent.name}</p>
                <p className="text-xs font-medium text-green">• Active</p>
              </div>
            </div>
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-cyan text-xs font-bold text-cyan"
              aria-hidden
            >
              {agent.gauge}%
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <div className="rounded-xl bg-black/30 p-3">
              <p className="text-[0.65rem] uppercase tracking-wide text-white/50">Confidence</p>
              <p className="mt-1 font-semibold text-white">{agent.confidence}</p>
            </div>
            <div className="rounded-xl bg-black/30 p-3">
              <p className="text-[0.65rem] uppercase tracking-wide text-white/50">Tasks completed</p>
              <p className="mt-1 font-semibold text-white">{agent.tasks}</p>
            </div>
          </div>
          <div className="mt-3 rounded-xl bg-black/30 p-3">
            <p className="text-[0.65rem] uppercase tracking-wide text-white/50">Recommendation</p>
            <p className="mt-1 text-sm text-white/90">{agent.recommendation}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

export function PlatformPills() {
  return (
    <div className="mx-auto mt-10 flex max-w-4xl flex-wrap justify-center gap-2 px-5">
      {PLATFORM_PILLS.map((pill) => (
        <Link
          key={pill.label}
          href={pill.href}
          className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-2 text-sm text-white/85 hover:bg-white/10"
        >
          <pill.icon size={14} />
          {pill.label}
        </Link>
      ))}
    </div>
  );
}

export function GetStartedCTA() {
  return (
    <section className="bg-midnight px-5 py-20 text-center text-white lg:px-8">
      <p className="text-eyebrow uppercase text-cyan">Get started</p>
      <h2 className="mx-auto mt-4 max-w-3xl text-4xl font-bold lg:text-5xl">
        Ready to Build an <span className="text-brand-gradient">AI-Driven Business?</span>
      </h2>
      <p className="mx-auto mt-4 max-w-2xl text-white/70">
        See how Decision.ai can predict outcomes, optimize decisions, and grow your business with continuous AI
        intelligence.
      </p>
      <div className="mt-8 flex flex-wrap justify-center gap-3">
        <Link
          href={BOOK_A_DEMO_HREF}
          className="bg-brand-gradient shadow-brand inline-flex items-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-white"
        >
          Book a Demo <ArrowRight size={16} />
        </Link>
        <Link href="/platform" className="rounded-full border border-white/20 px-6 py-3 text-sm font-semibold text-white">
          Explore the Platform
        </Link>
      </div>
      <p className="mt-4 text-xs text-white/50">No commitment. 30-minute walkthrough. See your business through AI.</p>
    </section>
  );
}

export function PricingGrid() {
  return (
    <div className="mx-auto mt-12 grid max-w-6xl gap-6 px-5 lg:grid-cols-3 lg:px-8">
      <PriceCard
        name="Starter"
        price="$499"
        period="/mo"
        blurb="For small teams getting started with AI."
        features={["3 AI Agents", "5 Integrations", "Weekly Reports", "Standard ML Models", "Email Support"]}
        cta="Start Free Trial →"
        href="/app/opportunities/upload"
        variant="light"
      />
      <PriceCard
        name="Growth"
        price="$1,999"
        period="/mo"
        blurb="Most popular — for scaling businesses."
        features={[
          "All 6 AI Agents",
          "Unlimited Integrations",
          "Real-time Dashboard",
          "Advanced ML Models",
          "Custom Predictions",
          "Priority Support",
        ]}
        cta="Book a Demo →"
        href={BOOK_A_DEMO_HREF}
        variant="popular"
        badge="Most Popular"
      />
      <PriceCard
        name="Enterprise"
        price="Custom"
        period=""
        blurb="Custom AI infrastructure for large organizations."
        features={[
          "Dedicated AI Infrastructure",
          "Custom Agent Development",
          "On-premise Deployment",
          "White-glove Onboarding",
          "SLA Guarantee",
          "Dedicated AI Strategist",
        ]}
        cta="Contact Sales →"
        href={BOOK_A_DEMO_HREF}
        variant="dark"
      />
    </div>
  );
}

function PriceCard({
  name,
  price,
  period,
  blurb,
  features,
  cta,
  href,
  variant,
  badge,
}: {
  name: string;
  price: string;
  period: string;
  blurb: string;
  features: string[];
  cta: string;
  href: string;
  variant: "light" | "popular" | "dark";
  badge?: string;
}) {
  const box =
    variant === "dark"
      ? "bg-midnight text-white"
      : variant === "popular"
        ? "bg-paper-raised ring-2 ring-brand shadow-brand"
        : "bg-paper-raised shadow-sm ring-1 ring-hairline";
  const muted = variant === "dark" ? "text-white/70" : "text-ink-muted";
  const title = variant === "dark" ? "text-white" : "text-ink";
  const accent = variant === "dark" ? "text-cyan" : "text-brand";
  const button =
    variant === "popular"
      ? "bg-brand-gradient text-white"
      : variant === "dark"
        ? "bg-white text-ink"
        : "border border-hairline bg-paper text-ink";

  return (
    <article className={`relative flex flex-col rounded-3xl p-8 ${box}`}>
      {badge ? (
        <span className="bg-brand-gradient absolute -top-3 left-1/2 -translate-x-1/2 rounded-full px-3 py-1 text-xs font-semibold text-white">
          {badge}
        </span>
      ) : null}
      <h3 className={`text-lg font-bold ${title}`}>{name}</h3>
      <p className="mt-3">
        <span className={`text-4xl font-bold ${title}`}>{price}</span>
        <span className={muted}>{period}</span>
      </p>
      <p className={`mt-3 text-sm ${muted}`}>{blurb}</p>
      <ul className="mt-6 flex-1 space-y-3">
        {features.map((item) => (
          <li key={item} className={`flex items-start gap-2 text-sm ${title}`}>
            <Check size={16} className={`mt-0.5 shrink-0 ${accent}`} />
            {item}
          </li>
        ))}
      </ul>
      <Link href={href} className={`mt-8 rounded-full px-5 py-3 text-center text-sm font-semibold ${button}`}>
        {cta}
      </Link>
    </article>
  );
}
