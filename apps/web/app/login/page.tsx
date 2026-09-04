"use client";

import { Button } from "@/app/components/ui/Button";
import { useLogin, useSession } from "@/lib/application";
import { displayName, isPlatformRole, roleLabel } from "@/lib/infrastructure/session";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const ACCOUNTS = [
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
          const businessMember = data.user.role === "business_admin" || data.user.role === "business_developer";
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

  if (!loaded) return null;

  if (user) {
    const home = isPlatformRole(user.role) ? "/admin/businesses" : user.role === "business_admin" || user.role === "business_developer" ? "/business" : "/app/dashboards";
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-5">
        <h1 className="font-display text-title text-ink">You are signed in</h1>
        <p className="mt-4 font-body text-body text-ink">
          Signed in as <span className="font-medium">{displayName(user)}</span>
        </p>
        <p className="mt-1 font-body text-body text-ink-muted">
          {user.email} · {roleLabel(user.role)}
        </p>
        <p className="mt-4 font-body text-body text-ink-muted">
          This session stays active until you sign out.
        </p>
        <div className="mt-8 flex flex-col gap-3">
          <Button
            type="button"
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
    <div className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-5">
      <h1 className="font-display text-title text-ink">Sign in</h1>
      <p className="mt-2 font-body text-body text-ink-muted">
        Two local accounts. A Business Client cannot open Admin pages. Admin can open both.
      </p>
      <form className="mt-8 space-y-4" onSubmit={onSubmit}>
        <label className="block font-body text-body text-ink">
          Email
          <input
            type="email"
            required
            autoComplete="username"
            className="mt-2 w-full rounded border border-hairline bg-paper-raised px-3 py-2 font-body text-ink"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className="block font-body text-body text-ink">
          Password
          <input
            type="password"
            required
            autoComplete="current-password"
            className="mt-2 w-full rounded border border-hairline bg-paper-raised px-3 py-2 font-body text-ink"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <Button type="submit" className="w-full" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
      {login.isError ? (
        <p className="mt-4 font-body text-body text-oxblood">
          {login.error instanceof Error ? login.error.message : "Could not sign in."}
        </p>
      ) : null}
      <ul className="mt-10 space-y-3">
        {ACCOUNTS.map((account) => (
          <li key={account.email} className="rounded bg-paper-raised p-4">
            <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{account.label}</p>
            <p className="mt-2 font-mono text-data text-ink-muted">
              {account.email} · {account.password}
            </p>
            <p className="mt-1 font-body text-body text-ink-muted">Opens {account.lands}.</p>
            <button
              type="button"
              className="mt-3 font-body text-body text-navy underline-offset-2 hover:underline"
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
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
