"use client";

import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useSession } from "@/lib/application";
import { displayName, roleLabel } from "@/lib/infrastructure/session";

export default function AccountPage() {
  const { user, loaded } = useSession();

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Account"
        description="Signed-in identity for this session. Profile and workspace membership changes are not available here."
      />
      {!loaded ? (
        <Skeleton className="h-48" />
      ) : !user ? (
        <p className="text-body text-ink-muted">Sign in to see the account for this session.</p>
      ) : (
        <Panel title="Session">
          <FactGrid>
            <Fact label="Name" value={displayName(user)} />
            <Fact label="Email" value={user.email} mono />
            <Fact label="Role" value={roleLabel(user.role)} />
            <Fact label="Workspace" value={user.workspace_id ?? "None assigned"} mono />
          </FactGrid>
        </Panel>
      )}
    </div>
  );
}
