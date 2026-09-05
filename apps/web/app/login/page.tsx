"use client";

import { defaultProductRoute } from "@/app/components/layout/app-navigation";
import { Button } from "@/app/components/ui/Button";
import { Field } from "@/app/components/ui/Field";
import { Spinner } from "@/app/components/ui/Spinner";
import { controlErrorClass } from "@/app/components/ui/control";
import { cn } from "@/lib/cn";
import { useLogin, useSession } from "@/lib/application";
import { displayName, isBusinessAdministrationRole, isPlatformRole, roleLabel } from "@/lib/infrastructure/session";
import { Lock, Mail } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const LOCAL_ACCOUNTS = [
  {
    label: "Business Client",
    email: "demo@client.io",
    password: "ClientPass123",
    lands: "the workspace (dashboards, opportunities, decisions)",
  },
  {
    label: "Admin",
    email: "admin@dclab.io",
    password: "AdminPass123",
    lands: "Labs and the rest of the staff area",
  },
] as const;

const SHOW_LOCAL_ACCOUNTS = process.env.NODE_ENV !== "production";

function authInputClass(invalid?: boolean) {
  return cn(
    "auth-field-input w-full rounded-md border border-hairline bg-paper-raised pl-10 pr-3 text-body text-ink shadow-xs transition-ui placeholder:text-ink-muted disabled:cursor-not-allowed disabled:opacity-50",
    invalid && controlErrorClass,
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const { user, loaded, signOut } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    login.mutate(
      { email, password },
      {
        onSuccess: (data) => {
          const requested = params.get("next");
          const platformMember = isPlatformRole(data.user.role);
          const businessMember = isBusinessAdministrationRole(data.user.role);
          const fallback = platformMember ? "/admin/businesses" : businessMember ? "/business" : "/app/dashboards";
          const allowed = requested && (
            platformMember ||
            (!requested.startsWith("/admin") && (businessMember || !requested.startsWith("/business")))
          );
          router.push(allowed && requested ? requested : fallback);
          router.refresh();
        },
      },
    );
  }

  const errorMessage = login.isError
    ? login.error instanceof Error
      ? login.error.message
      : "Could not sign in."
    : undefined;

  if (!loaded) {
    return (
      <div className="auth-panel">
        <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-ink-muted">
          <Spinner label="Loading session" />
          <p className="text-body">Checking session…</p>
        </div>
      </div>
    );
  }

  if (user) {
    const home = defaultProductRoute(user.role);
    return (
      <div className="auth-panel">
        <h1 className="text-title text-ink">You are signed in</h1>
        <p className="mt-2 text-body text-ink-muted">
          Signed in as <span className="font-medium text-ink">{displayName(user)}</span>
        </p>
        <p className="mt-1 text-helper text-ink-muted">
          {user.email} · {roleLabel(user.role)}
        </p>
        <p className="mt-4 text-body text-ink-muted">This session stays active until you sign out.</p>
        <div className="mt-8 flex flex-col gap-3">
          <Button
            type="button"
            className="w-full"
            size="xl"
            onClick={() => {
              router.push(home);
              router.refresh();
            }}
          >
            Continue
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-full"
            size="xl"
            onClick={() => {
              signOut();
              router.refresh();
            }}
          >
            Sign out and use a different account
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-panel">
      <h1 className="text-title text-ink">Sign in</h1>
      <p className="mt-2 text-body text-ink-muted">Use your Decision.ai account to open your workspace.</p>
      <form className="mt-8 space-y-4" onSubmit={onSubmit}>
        <Field label="Email" htmlFor="email">
          <div className="auth-field">
            <Mail className="auth-field-icon" size={16} strokeWidth={1.8} aria-hidden />
            <input
              id="email"
              name="email"
              type="email"
              required
              autoComplete="username"
              placeholder="you@example.com"
              aria-invalid={errorMessage ? true : undefined}
              aria-describedby={errorMessage ? "login-error" : undefined}
              className={authInputClass(Boolean(errorMessage))}
              value={email}
              disabled={login.isPending}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
        </Field>
        <Field label="Password" htmlFor="password">
          <div className="auth-field">
            <Lock className="auth-field-icon" size={16} strokeWidth={1.8} aria-hidden />
            <input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
              placeholder="••••••••"
              aria-invalid={errorMessage ? true : undefined}
              aria-describedby={errorMessage ? "login-error" : undefined}
              className={authInputClass(Boolean(errorMessage))}
              value={password}
              disabled={login.isPending}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
        </Field>
        {errorMessage ? (
          <p id="login-error" className="text-helper text-oxblood" role="alert">
            {errorMessage}
          </p>
        ) : null}
        <Button type="submit" className="w-full" size="xl" loading={login.isPending}>
          Sign in
        </Button>
      </form>
      {SHOW_LOCAL_ACCOUNTS ? (
        <details className="mt-8 rounded-xl border border-hairline bg-paper px-4 py-3">
          <summary className="cursor-pointer text-helper font-medium text-ink-muted">Local development accounts</summary>
          <ul className="mt-3 space-y-3">
            {LOCAL_ACCOUNTS.map((account) => (
              <li key={account.email}>
                <p className="text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{account.label}</p>
                <p className="mt-1 font-mono text-data text-ink-muted">
                  {account.email} · {account.password}
                </p>
                <p className="mt-1 text-helper text-ink-muted">Opens {account.lands}.</p>
                <button
                  type="button"
                  className="mt-2 text-helper font-medium text-navy underline-offset-2 hover:underline"
                  onClick={() => {
                    setEmail(account.email);
                    setPassword(account.password);
                  }}
                >
                  Use this account
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="auth-panel">
          <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-ink-muted">
            <Spinner label="Loading" />
          </div>
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
