"use client";

import { useSession } from "@/lib/application";
import { defaultProductRoute } from "@/lib/infrastructure/capabilities";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

const STORY = [
  {
    title: "Your agent drives",
    body: "Claude Code, Codex or any MCP client connects with a scoped token and works the lab: data, splits, features, experiments.",
  },
  {
    title: "The lab proves",
    body: "Every model is checked for leakage, scored on a final holdout it never trained on, and logged with its evidence.",
  },
  {
    title: "You decide",
    body: "Each choice is a record with a rule answer beside any AI answer. Nothing changes without a trail you can read.",
  },
] as const;

export default function HomePage() {
  const { user, loaded } = useSession();
  const signedIn = loaded && Boolean(user);
  const href = signedIn ? defaultProductRoute(user) : "/login";
  const label = signedIn ? "Open your workspace" : "Sign in";

  return (
    <section className="bg-transparent">
      <div className="marketing-wrap py-20 lg:py-32">
        <div className="mx-auto max-w-3xl">
          <p className="text-eyebrow uppercase tracking-[0.18em] text-teal-700">DCLab</p>
          <h1 className="mt-5 text-[2.5rem] font-semibold leading-[1.1] tracking-tight text-ink sm:text-6xl">
            An ML lab your AI agent can drive, which proves every model is correct.
          </h1>
          <p className="mt-6 max-w-2xl text-xl leading-8 text-ink-muted">
            Agents and people work on the same versioned record of the problem, the data, the experiments and the models.
            The lab runs the checks. You see the evidence.
          </p>
          <div className="mt-10">
            <Link
              href={href}
              data-testid="home-cta"
              className="inline-flex items-center gap-2 rounded-full bg-teal-700 px-7 py-3.5 text-lg font-medium text-white transition-colors hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
            >
              {label} <ArrowRight size={20} aria-hidden />
            </Link>
          </div>
        </div>
        <ol className="mx-auto mt-20 grid max-w-5xl gap-10 border-t border-hairline pt-12 md:grid-cols-3">
          {STORY.map((step, index) => (
            <li key={step.title}>
              <p className="text-sm font-medium text-teal-700">Step {index + 1}</p>
              <h2 className="mt-2 text-2xl font-semibold text-ink">{step.title}</h2>
              <p className="mt-3 text-lg leading-7 text-ink-muted">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
