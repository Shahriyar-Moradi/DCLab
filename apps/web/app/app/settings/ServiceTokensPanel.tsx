"use client";

import { useState, type FormEvent } from "react";
import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { Panel } from "@/app/components/ui/Card";
import { Checkbox } from "@/app/components/ui/Checkbox";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Input } from "@/app/components/ui/Input";
import { Select } from "@/app/components/ui/Select";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useCreateServiceToken, useRevokeServiceToken, useServiceTokens } from "@/lib/application";
import { SERVICE_TOKEN_SCOPES, formatTimestamp, type ServiceTokenScope } from "@/lib/domain";
import { newIdempotencyKey } from "@/lib/infrastructure/api-client";

const STATUS_TONE = { active: "green", expired: "amber", revoked: "oxblood" } as const;
const EXPIRY_DAYS = ["7", "30", "90"] as const;

/** Create (secret shown once), list and revoke /v1 service tokens for SDK / MCP clients. */
export function ServiceTokensPanel() {
  const tokens = useServiceTokens();
  const create = useCreateServiceToken();
  const revoke = useRevokeServiceToken();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ServiceTokenScope[]>(["read"]);
  const [days, setDays] = useState<string>("30");
  const [password, setPassword] = useState("");
  // The secret lives only in the mutation result until "Done" resets it.
  const secret = create.data?.secret ?? null;

  function toggle(scope: ServiceTokenScope, checked: boolean) {
    setScopes((current) => (checked ? [...current, scope] : current.filter((item) => item !== scope)));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const currentPassword = password;
    setPassword("");
    create.mutate(
      {
        name: name.trim(),
        scopes,
        expires_in_days: Number(days),
        current_password: currentPassword,
        idempotencyKey: newIdempotencyKey(),
      },
      { onSuccess: () => setName("") },
    );
  }

  return (
    <Panel
      title="Service tokens"
      description="Bearer credentials for the SDK and MCP clients. A token acts only in this workspace, only within its scopes and your current role, and never in the browser."
      className="mt-6"
    >
      <form onSubmit={submit} className="grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
        <Input id="token-name" label="Name" value={name} maxLength={80} required onChange={(event) => setName(event.target.value)} />
        <Select id="token-expiry" label="Expires in" value={days} onChange={(event) => setDays(event.target.value)}>
          {EXPIRY_DAYS.map((value) => (
            <option key={value} value={value}>
              {value} days
            </option>
          ))}
        </Select>
        <div className="md:col-span-2">
          <Input
            id="token-password"
            label="Current password"
            hint="Required to create a token."
            type="password"
            autoComplete="current-password"
            value={password}
            required
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <fieldset className="flex flex-wrap gap-4 md:col-span-2">
          <legend className="mb-2 font-sans text-label uppercase text-ink-muted">Scopes</legend>
          {SERVICE_TOKEN_SCOPES.map((scope) => (
            <Checkbox
              key={scope}
              id={`scope-${scope}`}
              label={scope}
              checked={scopes.includes(scope)}
              onChange={(event) => toggle(scope, event.target.checked)}
            />
          ))}
        </fieldset>
        <div className="md:col-span-2">
          <Button
            type="submit"
            loading={create.isPending}
            disabled={!name.trim() || scopes.length === 0 || !password}
          >
            Create token
          </Button>
          {create.error ? <p className="mt-2 text-body text-oxblood">{create.error.message}</p> : null}
        </div>
      </form>

      {secret ? (
        <div role="status" className="mt-4 rounded-md border border-hairline bg-navy-soft p-4">
          <p className="text-body text-ink">Copy this token now. It will not be shown again.</p>
          <code className="mt-2 block break-all font-mono text-data text-ink">{secret}</code>
          <div className="mt-3 flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => void navigator.clipboard?.writeText(secret)}>
              Copy
            </Button>
            <Button size="sm" variant="ghost" onClick={() => create.reset()}>
              Done
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-6">
        {tokens.isLoading ? (
          <Skeleton className="h-24" />
        ) : tokens.error ? (
          <ErrorState body={tokens.error.message} onRetry={() => void tokens.refetch()} />
        ) : !tokens.data?.length ? (
          <p className="text-body text-ink-muted">No service tokens in this workspace.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Token</Th>
                <Th>Scopes</Th>
                <Th>Status</Th>
                <Th>Expires</Th>
                <Th>Last used</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {tokens.data.map((token) => (
                <tr key={token.id}>
                  <Td>{token.name}</Td>
                  <Td mono>{token.prefix}…</Td>
                  <Td>{token.scopes.join(", ")}</Td>
                  <Td>
                    <Badge tone={STATUS_TONE[token.status]}>{token.status}</Badge>
                  </Td>
                  <Td>{formatTimestamp(token.expires_at)}</Td>
                  <Td>{token.last_used_at ? formatTimestamp(token.last_used_at) : "Never"}</Td>
                  <Td>
                    {token.status === "active" ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={revoke.isPending && revoke.variables === token.id}
                        onClick={() => revoke.mutate(token.id)}
                      >
                        Revoke
                      </Button>
                    ) : null}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {revoke.error ? <p className="mt-2 text-body text-oxblood">{revoke.error.message}</p> : null}
      </div>
    </Panel>
  );
}
