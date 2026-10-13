/**
 * Business view (P7.6-A): pure view-model logic for `/outcomes`, so it runs under `npm run test:components`.
 * Every outcome sentence is built by the API (`GET /v1/projects/{id}/outcomes`) from numbers with fixed templates;
 * this module only chooses the project, tells the states apart (loading, failed, no project with a model in use,
 * no model in use, no final test result, ready) and caps every API string as plain text. The page words below are
 * the only words written here; a test loops over all of them with the business-view word rules.
 */
import { isUuid, plainText } from "./command-search.ts";
import { safeDownloadId } from "./studio-scoring.ts";

export const WORDS = {
  eyebrow: "Business view",
  guidePurpose: "What the model in use does for your business, in plain words.",
  guideHowTo: "Read the outcome first, then download the latest list.",
  guideYouGet: "How well it did when it was tested, how it was built, the latest list and what it cannot do.",
  outcomeTitle: "What it does for you",
  builtTitle: "How it was built",
  listTitle: "The latest list",
  limitsTitle: "What it cannot do",
  projectLabel: "Project",
  download: "Download the list",
  downloading: "Preparing the file…",
  scoredOn: "Scored on",
  savedOn: "Saved on",
  approvedOn: "Approved for scoring on",
  loading: "Loading the business view…",
  failed: "The business view could not be loaded. Try again in a moment.",
  notFound: "This project was not found in your workspace.",
  noProjects: "No project in this workspace has a model in use yet. A data scientist in your workspace can put one in use.",
  downloadFailed: "The list could not be downloaded. Try again in a moment.",
  badLink: "The download link was not a DCLab address, so it was not opened.",
  planned: "Planned, not available yet: questions from your data team, and asking questions in plain words.",
  boundaryLabel: "Boundary",
} as const;

const SENTENCE = 400;
const NAME = 80;

export type HeadlineLike = {
  status: "reported" | "withheld" | "missing";
  sentences?: string[] | null;
  counts_text?: string | null;
  basis: string;
};
export type OutcomesLike = {
  project_name: string;
  state: "ready" | "no_model" | "no_result";
  message?: string | null;
  model_in_use?: { display_name: string; saved_at: string; approved_at?: string | null; approval_text: string } | null;
  headline?: HeadlineLike | null;
  how_built?: { sentences?: string[] | null } | null;
  weekly_list?: { id: string; completed_at: string; rows: number; has_reasons: boolean; text: string; download_path: string } | null;
  weekly_list_note?: string | null;
  limits?: string[] | null;
  boundary: string;
};

export type ProjectChoice = { id: string; name: string };
type ProjectLike = { id: string; name: string };

/** The API's projects with a model in use, in its order; ids must be UUIDs. */
export function projectChoices(list: { items?: readonly ProjectLike[] } | undefined): ProjectChoice[] {
  return (list?.items ?? [])
    .filter((project) => isUuid(project.id))
    .map((project) => ({ id: project.id, name: plainText(project.name, NAME) || "Project" }));
}

/** The requested project when it is one of the choices, else the first choice. */
export function pickProject(choices: readonly ProjectChoice[], requested: string | null | undefined): string | null {
  if (requested && isUuid(requested) && choices.some((choice) => choice.id === requested)) return requested;
  return choices[0]?.id ?? null;
}

/** "7 Oct 2026" (UTC, so every viewer reads the same day); unreadable dates are left out. */
export function formatDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

const lines = (values: readonly string[] | null | undefined): string[] =>
  (values ?? []).map((value) => plainText(value, SENTENCE)).filter(Boolean);

export type ReadyView = {
  kind: "ready";
  projectName: string;
  modelName: string | null;
  dates: string[];
  approval: string | null;
  /** The outcome sentences, or the plain reason there are none (no final test result, or withheld). */
  outcome: string[];
  outcomeNotice: string | null;
  counts: string | null;
  basis: string | null;
  built: string[];
  list: { id: string; scoredOn: string | null; text: string } | null;
  listNote: string | null;
  limits: string[];
  boundary: string;
};
export type OutcomesView =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "no_projects"; message: string }
  | { kind: "no_model"; projectName: string; message: string; boundary: string }
  | ReadyView;

export type OutcomesInput = {
  projectsPending: boolean;
  projectsFailed: boolean;
  choices: readonly ProjectChoice[];
  pending: boolean;
  failedStatus: number | null;
  data: OutcomesLike | undefined;
};

export function outcomesView(input: OutcomesInput): OutcomesView {
  if (input.projectsPending) return { kind: "loading" };
  if (input.projectsFailed) return { kind: "error", message: WORDS.failed };
  if (input.choices.length === 0) return { kind: "no_projects", message: WORDS.noProjects };
  if (input.failedStatus !== null) return { kind: "error", message: input.failedStatus === 404 ? WORDS.notFound : WORDS.failed };
  if (input.pending || !input.data) return { kind: "loading" };
  const d = input.data;
  const projectName = plainText(d.project_name, NAME) || "Project";
  const boundary = plainText(d.boundary, SENTENCE);
  if (d.state === "no_model" || !d.model_in_use) {
    return { kind: "no_model", projectName, message: plainText(d.message ?? "", SENTENCE), boundary };
  }
  const model = d.model_in_use;
  const headline = d.headline ?? null;
  const reported = headline?.status === "reported";
  const saved = formatDay(model.saved_at);
  const approved = formatDay(model.approved_at);
  const list = d.weekly_list ?? null;
  return {
    kind: "ready",
    projectName,
    modelName: plainText(model.display_name, NAME) || null,
    dates: [saved ? `${WORDS.savedOn} ${saved}` : null, approved ? `${WORDS.approvedOn} ${approved}` : null].filter(
      (value): value is string => value !== null,
    ),
    approval: plainText(model.approval_text, SENTENCE) || null,
    outcome: reported ? lines(headline?.sentences) : [],
    outcomeNotice: reported ? null : plainText(d.state === "no_result" ? d.message ?? headline?.basis : headline?.basis, SENTENCE) || null,
    counts: reported && headline?.counts_text ? plainText(headline.counts_text, SENTENCE) : null,
    basis: reported && headline ? plainText(headline.basis, SENTENCE) : null,
    built: lines(d.how_built?.sentences),
    list: list && isUuid(list.id) ? { id: list.id, scoredOn: formatDay(list.completed_at), text: plainText(list.text, SENTENCE) } : null,
    listNote: list ? null : plainText(d.weekly_list_note, SENTENCE) || null,
    limits: lines(d.limits),
    boundary,
  };
}

/** The prediction id to download, only when the API's path is exactly that prediction's DCLab download. */
export function listDownloadId(list: OutcomesLike["weekly_list"]): string | null {
  return list ? safeDownloadId(list.download_path, list.id) : null;
}
