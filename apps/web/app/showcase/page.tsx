"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { Card } from "@/app/components/ui/Card";
import { ConfidenceBar } from "@/app/components/ui/ConfidenceBar";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";

export default function ShowcasePage() {
  return (
    <div>
      <h1 className="font-display text-title text-ink">Primitives</h1>
      <p className="mt-2 font-body text-body text-ink-muted">Scratch page for Step 1 visual review.</p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Button>Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button disabled>Disabled</Button>
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <Badge tone="green">Contact today</Badge>
        <Badge tone="amber">Send email</Badge>
        <Badge tone="oxblood">No action</Badge>
      </div>
      <Card className="mt-8 max-w-md p-6">
        <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Confidence</p>
        <ConfidenceBar className="mt-3" value={0.86} tone="green" />
        <ConfidenceBar className="mt-3" value={0.61} tone="amber" />
        <ConfidenceBar className="mt-3" value={0.22} tone="oxblood" />
      </Card>
      <div className="mt-8">
        <Table>
          <thead>
            <tr>
              <Th sortable onSort={() => undefined}>
                Amount
              </Th>
              <Th>Stage</Th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <Td mono>AED 73,000</Td>
              <Td>proposal</Td>
            </tr>
          </tbody>
        </Table>
      </div>
      <div className="mt-8 grid gap-4">
        <Skeleton className="h-16" />
        <EmptyState title="Empty" body="Invitation to act." actionLabel="Upload" actionHref="/opportunities/upload" />
        <ErrorState body="A reportable failure." onRetry={() => undefined} />
      </div>
    </div>
  );
}
