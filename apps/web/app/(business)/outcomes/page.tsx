"use client";

/**
 * Business view (P7.6-A): what the model in use does, in plain words, for business users. Every outcome sentence
 * comes from `GET /v1/projects/{id}/outcomes` (built there from numbers); names and sentences render as plain text.
 * Sections: outcome, how it was built, the latest list (existing authorized download), limits, boundary.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { useSession } from "@/lib/application";
import { WORDS, listDownloadId, outcomesView, pickProject, projectChoices, type ReadyView } from "@/lib/application/business-outcomes";
import { downloadList, useOutcomeProjects, useProjectOutcomes } from "@/lib/application/business-outcomes-hooks";
import { isUuid } from "@/lib/application/command-search";
import { safeFilename } from "@/lib/application/studio-inspect";
import { ApiError } from "@/lib/infrastructure/api-client";

const BIG = { fontSize: "1.35rem", lineHeight: 1.45, fontWeight: 600 } as const;
const BODY = { fontSize: "1.1rem", lineHeight: 1.6 } as const;

function Download({ id }: { id: string }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const run = async () => {
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    setError(null);
    try {
      const { blob, filename } = await downloadList(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = safeFilename(filename, `list-${id}.csv`);
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError(WORDS.downloadFailed);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <p className="toolbar">
        <button type="button" className="btn primary" onClick={() => void run()} disabled={saving}>
          {saving ? WORDS.downloading : WORDS.download}
        </button>
      </p>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

function Ready({ view, downloadId }: { view: ReadyView; downloadId: string | null }) {
  return (
    <>
      <Card title={WORDS.outcomeTitle}>
        <div style={{ display: "grid", gap: 12 }}>
          {view.outcome.length ? view.outcome.map((line) => <p key={line} style={BIG}>{line}</p>) : null}
          {view.outcomeNotice ? <p style={BODY}>{view.outcomeNotice}</p> : null}
          {view.counts ? <p className="muted">{view.counts}</p> : null}
          {view.basis ? <p className="muted">{view.basis}</p> : null}
        </div>
      </Card>
      {view.built.length ? (
        <Card title={WORDS.builtTitle}>
          <div style={{ display: "grid", gap: 10 }}>
            {view.built.map((line) => <p key={line} style={BODY}>{line}</p>)}
          </div>
        </Card>
      ) : null}
      <Card title={WORDS.listTitle}>
        <div id="predictions" style={{ display: "grid", gap: 10 }}>
          {view.list ? (
            <>
              <p style={BODY}>{view.list.text}</p>
              {view.list.scoredOn ? <p className="muted">{WORDS.scoredOn} {view.list.scoredOn}</p> : null}
              {downloadId ? <Download id={downloadId} /> : <p role="alert">{WORDS.badLink}</p>}
            </>
          ) : (
            <p style={BODY}>{view.listNote}</p>
          )}
        </div>
      </Card>
      {view.limits.length ? (
        <Card title={WORDS.limitsTitle}>
          <ul style={{ ...BODY, margin: 0, paddingLeft: "1.2rem", display: "grid", gap: 8 }}>
            {view.limits.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </Card>
      ) : null}
    </>
  );
}

function OutcomesInner() {
  const router = useRouter();
  const { user } = useSession();
  const requested = useSearchParams().get("project");
  const projects = useOutcomeProjects(!!user);
  const choices = projectChoices(projects.data);
  const projectId = pickProject(choices, requested);
  const read = useProjectOutcomes(projectId);
  const view = outcomesView({
    projectsPending: projects.isPending,
    projectsFailed: projects.isError,
    choices,
    pending: read.isPending,
    failedStatus: read.isError ? (read.error instanceof ApiError ? read.error.status : 0) : null,
    data: read.data,
  });
  const choose = (id: string) => {
    if (isUuid(id)) router.replace(`/outcomes?project=${encodeURIComponent(id)}`);
  };

  const title = view.kind === "ready" || view.kind === "no_model" ? view.projectName : WORDS.eyebrow;
  const subtitle = view.kind === "ready" ? [view.modelName, ...view.dates].filter(Boolean).join(" · ") : undefined;
  return (
    <>
      <PageHead
        eyebrow={WORDS.eyebrow}
        title={title}
        subtitle={subtitle}
        actions={
          choices.length > 1 && projectId ? (
            <label className="field">
              <span>{WORDS.projectLabel}</span>
              <select value={projectId} onChange={(event) => choose(event.target.value)}>
                {choices.map((choice) => <option key={choice.id} value={choice.id}>{choice.name}</option>)}
              </select>
            </label>
          ) : null
        }
      />
      <PageGuide purpose={WORDS.guidePurpose} howTo={WORDS.guideHowTo} youGet={WORDS.guideYouGet} />
      {view.kind === "loading" ? <p role="status">{WORDS.loading}</p> : null}
      {view.kind === "error" ? <p role="alert">{view.message}</p> : null}
      {view.kind === "no_projects" ? <Card><p style={BODY}>{view.message}</p></Card> : null}
      {view.kind === "no_model" ? <Card><p style={BODY}>{view.message}</p></Card> : null}
      {view.kind === "ready" ? (
        <>
          {view.approval ? <p className="muted">{view.approval}</p> : null}
          <Ready view={view} downloadId={listDownloadId(read.data?.weekly_list)} />
        </>
      ) : null}
      {view.kind === "ready" || view.kind === "no_model" ? (
        <Banner>
          <strong>{WORDS.boundaryLabel}: </strong>
          {view.boundary}
        </Banner>
      ) : null}
      <p className="muted">{WORDS.planned}</p>
    </>
  );
}

export default function OutcomesPage() {
  return (
    <Suspense fallback={<p role="status">{WORDS.loading}</p>}>
      <OutcomesInner />
    </Suspense>
  );
}
