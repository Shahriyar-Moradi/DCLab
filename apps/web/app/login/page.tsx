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
  { label: "DCLab Admin", email: "admin@dclab.io", password: "AdminPass123" },
  { label: "DCLab Developer", email: "developer@dclab.io", password: "DeveloperPass123" },
  { label: "Business Client", email: "demo@client.io", password: "ClientPass123" },
  { label: "Business Admin", email: "business-admin@dclab.io", password: "BusinessAdminPass123" },
  { label: "Business Developer", email: "business-developer@dclab.io", password: "BusinessDevPass123" },
  { label: "Personal Developer", email: "personal@dclab.io", password: "PersonalPass123" },
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
      <p className="mt-2 text-body text-ink-muted">Use your DCLab account to open your workspace.</p>
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
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-helper">
              <thead>
                <tr className="text-ink-muted">
                  <th className="py-1 pr-3 font-medium">Role</th>
                  <th className="py-1 pr-3 font-medium">Email</th>
                  <th className="py-1 pr-3 font-medium">Password</th>
                  <th className="py-1 font-medium"><span className="sr-only">Use</span></th>
                </tr>
              </thead>
              <tbody>
                {LOCAL_ACCOUNTS.map((account) => (
                  <tr key={account.email} className="align-top text-ink">
                    <td className="py-2 pr-3 font-medium">{account.label}</td>
                    <td className="py-2 pr-3 font-mono text-data">{account.email}</td>
                    <td className="py-2 pr-3 font-mono text-data">{account.password}</td>
                    <td className="py-2">
                      <button
                        type="button"
                        className="font-medium text-navy underline-offset-2 hover:underline"
                        onClick={() => {
                          setEmail(account.email);
                          setPassword(account.password);
                        }}
                      >
                        Use
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
