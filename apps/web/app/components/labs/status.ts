import type { LabRunStatus } from "@/lib/domain";

export const OPEN_FILE_ACCEPT = [
  ".csv",
  ".tsv",
  ".tab",
  ".json",
  ".jsonl",
  ".ndjson",
  ".parquet",
  ".pq",
  ".xlsx",
  ".xls",
  ".txt",
  ".log",
  "text/csv",
  "text/plain",
  "application/json",
].join(",");

export const KIND_LABELS: Record<string, string> = {
  spreadsheet: "spreadsheet",
  json: "JSON",
  table_file: "table file",
  plain_text: "plain text",
};

export const LAB_RUN_STATUS_LABEL: Record<LabRunStatus, string> = {
  queued: "Queued",
  processing: "In progress",
  completed: "Completed",
  failed: "Could not finish",
};

export function runPath(runId: string): string {
  return `/lab/runs/${runId}`;
}

export function parseDelimitedHeader(text: string, delimiter: string): string[] {
  const fields: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === delimiter && !quoted) {
      fields.push(field.trim());
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      break;
    } else {
      field += character;
    }
  }
  fields.push(field.trim());
  return Array.from(new Set(fields.map((value) => value.replace(/^\uFEFF/, "")).filter(Boolean)));
}

export async function targetOptionsFor(file: File): Promise<string[]> {
  const suffix = file.name.split(".").pop()?.toLowerCase();
  if (!suffix || !["csv", "tsv", "tab"].includes(suffix)) return [];
  const text = await file.slice(0, 64 * 1024).text();
  return parseDelimitedHeader(text, suffix === "csv" ? "," : "\t");
}
