"use client";

import {
  ActionMenu,
  Badge,
  Button,
  Card,
  Checkbox,
  ConfidenceBar,
  DataTable,
  Dialog,
  Drawer,
  EmptyState,
  ErrorState,
  FilterBar,
  GlassPanel,
  IconButton,
  Input,
  LoadingState,
  MetricCard,
  PageHeader,
  Pagination,
  Panel,
  Radio,
  RadioGroup,
  SearchInput,
  SectionHeader,
  Select,
  Skeleton,
  StatusBadge,
  Switch,
  TabPanel,
  Tabs,
  Textarea,
  Tooltip,
  UploadZone,
} from "@/app/components/ui";
import { Bell } from "lucide-react";
import { useMemo, useState } from "react";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "done", label: "Done" },
];

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "detail", label: "Detail" },
];

const TABLE_ROWS = [
  { id: "1", name: "Northwind renewal", amount: "AED 73,000", stage: "proposal" },
  { id: "2", name: "Helios expansion", amount: "AED 18,400", stage: "qualification" },
];

export function ShowcaseCatalog() {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("overview");
  const [page, setPage] = useState(1);
  const [checked, setChecked] = useState(true);
  const [switched, setSwitched] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sortId, setSortId] = useState("name");

  const rows = useMemo(
    () => TABLE_ROWS.filter((row) => row.name.toLowerCase().includes(query.toLowerCase())),
    [query],
  );

  return (
    <div className="space-y-12 pb-16">
      <PageHeader
        eyebrow="Internal"
        title="UI primitives"
        description="Development catalog for shared components. Values here are local UI fixtures, not API data."
        breadcrumbs={[
          { label: "Workspace", href: "/app/dashboards" },
          { label: "Primitives" },
        ]}
        status={{ label: "Development", tone: "amber" }}
      />

      <section className="space-y-4">
        <SectionHeader title="Actions" description="Buttons, icon buttons, and menus." />
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button disabled>Disabled</Button>
          <Button loading>Saving</Button>
          <Tooltip content="Workspace alerts">
            <IconButton label="Notifications">
              <Bell size={16} aria-hidden />
            </IconButton>
          </Tooltip>
          <ActionMenu
            label="Actions"
            items={[
              { id: "copy", label: "Copy id", onSelect: () => undefined },
              { id: "remove", label: "Remove", destructive: true, onSelect: () => undefined },
            ]}
          />
        </div>
      </section>

      <section className="space-y-4">
        <SectionHeader title="Status" />
        <div className="flex flex-wrap gap-2">
          <Badge tone="green">Contact today</Badge>
          <Badge tone="amber">Send email</Badge>
          <Badge tone="oxblood">No action</Badge>
          <Badge tone="neutral" emphasis="soft">
            Draft
          </Badge>
          <StatusBadge status="completed" />
          <StatusBadge status="failed" />
        </div>
        <Card className="max-w-md p-5">
          <p className="product-eyebrow">Confidence</p>
          <ConfidenceBar className="mt-3" value={0.86} tone="green" />
          <ConfidenceBar className="mt-3" value={0.61} tone="amber" />
          <ConfidenceBar className="mt-3" value={0.22} tone="oxblood" />
        </Card>
      </section>

      <section className="space-y-4">
        <SectionHeader title="Metrics and panels" />
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCard label="Opportunities" value="24" hint="In the pipeline" />
          <MetricCard label="Decisions" value="12" tone="brand" />
          <MetricCard label="Needs review" value="3" tone="warning" />
        </div>
        <GlassPanel title="Glass panel" description="Reserved for chrome and selected overlays.">
          <p className="text-body text-ink-muted">Not used for every card.</p>
        </GlassPanel>
        <Panel title="Solid panel" description="Default raised surface.">
          <p className="text-body text-ink">Body copy sits on white.</p>
        </Panel>
      </section>

      <section className="space-y-4">
        <SectionHeader title="Forms" />
        <div className="grid max-w-xl gap-4">
          <Input id="showcase-email" label="Email" placeholder="you@company.com" />
          <Input id="showcase-error" label="Workspace slug" error="This slug is already taken." defaultValue="acme" />
          <Select id="showcase-role" label="Role" defaultValue="viewer">
            <option value="viewer">Viewer</option>
            <option value="engineer">Engineer</option>
          </Select>
          <Textarea id="showcase-notes" label="Notes" hint="Plain text only." />
          <Checkbox id="showcase-check" label="Notify me" defaultChecked={checked} onChange={(event) => setChecked(event.target.checked)} />
          <Switch checked={switched} onCheckedChange={setSwitched} label="Compact density" />
          <RadioGroup legend="Horizon">
            <Radio name="horizon" label="7 days" defaultChecked />
            <Radio name="horizon" label="30 days" />
          </RadioGroup>
        </div>
      </section>

      <section className="space-y-4">
        <SectionHeader title="Filters and tables" />
        <FilterBar
          options={FILTERS}
          value={filter}
          onChange={setFilter}
          trailing={<SearchInput value={query} onChange={setQuery} placeholder="Filter rows" />}
        />
        <DataTable
          columns={[
            { id: "name", header: "Name", sortable: true, cell: (row) => row.name },
            { id: "amount", header: "Amount", mono: true, cell: (row) => row.amount },
            { id: "stage", header: "Stage", cell: (row) => row.stage },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          sortId={sortId}
          onSort={setSortId}
        />
        <Pagination page={page} pageCount={4} onPageChange={setPage} />
      </section>

      <section className="space-y-4">
        <SectionHeader title="Tabs and overlays" />
        <Tabs items={TABS} value={tab} onChange={setTab} />
        <TabPanel id="overview" value={tab}>
          <p className="text-body text-ink-muted">Overview panel.</p>
        </TabPanel>
        <TabPanel id="detail" value={tab}>
          <p className="text-body text-ink-muted">Detail panel.</p>
        </TabPanel>
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => setDialogOpen(true)}>
            Open dialog
          </Button>
          <Button variant="secondary" onClick={() => setDrawerOpen(true)}>
            Open drawer
          </Button>
        </div>
        <Dialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title="Confirm action"
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={() => setDialogOpen(false)}>Continue</Button>
            </>
          }
        >
          This dialog has no backend. It only demonstrates focus trapping and Escape.
        </Dialog>
        <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Inspector">
          Drawer content. Closed with Escape, the overlay, or the close button.
        </Drawer>
      </section>

      <section className="space-y-4">
        <SectionHeader title="Feedback" />
        <Skeleton className="h-16" label="Loading block" />
        <LoadingState label="Loading workspace" />
        <EmptyState title="Empty" body="Invitation to act." actionLabel="Upload" actionHref="/app/opportunities/upload" />
        <ErrorState body="A reportable failure." onRetry={() => undefined} />
        <UploadZone
          accept=".csv,text/csv"
          hint="CSV only. The file stays in this browser until a page connects the callback."
          onFiles={() => undefined}
        />
      </section>
    </div>
  );
}
