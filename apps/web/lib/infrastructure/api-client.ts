import type { ZodType } from "zod";
import { clearToken, readToken } from "./session";

/** Browser origin for FastAPI. Set in `apps/web/.env.local` as NEXT_PUBLIC_API_URL. */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function buildUrl(path: string, params?: Record<string, string | number | boolean | undefined>): string {
  const url = new URL(path, API_URL);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function authHeaders(): Record<string, string> {
  const token = readToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const response = await fetch(buildUrl(path, params), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...authHeaders(),
      ...init?.headers,
    },
  });
  const body = await parseJson(response);
  if (!response.ok) {
    // An expired or revoked token should drop the session rather than leave the
    // user clicking through screens that will keep failing.
    if (response.status === 401) clearToken();
    const detail =
      typeof body === "object" && body && "detail" in body ? String((body as { detail: unknown }).detail) : response.statusText;
    throw new ApiError(response.status, body, detail || `Request failed (${response.status})`);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new ApiError(response.status, parsed.error.flatten(), "The API returned a response this app could not read.");
  }
  return parsed.data;
}

export function apiGet<T>(
  path: string,
  schema: ZodType<T>,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  return request(path, schema, { method: "GET" }, params);
}

export async function apiDownload(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(buildUrl(path), {
    headers: {
      Accept: "text/csv",
      ...authHeaders(),
    },
  });
  if (!response.ok) {
    if (response.status === 401) clearToken();
    throw new ApiError(response.status, null, response.statusText || `Request failed (${response.status})`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return {
    blob: await response.blob(),
    filename: match?.[1] || "predictions.csv",
  };
}

export function apiPost<T>(path: string, schema: ZodType<T>, json: unknown): Promise<T> {
  return request(path, schema, { method: "POST", body: JSON.stringify(json) });
}

export function apiPostForm<T>(path: string, schema: ZodType<T>, form: FormData): Promise<T> {
  return request(path, schema, { method: "POST", body: form });
}

export function uploadFile<T>(
  path: string,
  schema: ZodType<T>,
  file: File,
  onProgress?: (percent: number) => void,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildUrl(path, params));
    xhr.responseType = "text";
    const token = readToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (!onProgress || !event.lengthComputable) return;
      onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      let body: unknown = xhr.responseText;
      try {
        body = JSON.parse(xhr.responseText) as unknown;
      } catch {
        /* keep text */
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        if (xhr.status === 401) clearToken();
        reject(new ApiError(xhr.status, body, "Upload failed"));
        return;
      }
      const parsed = schema.safeParse(body);
      if (!parsed.success) {
        reject(new ApiError(xhr.status, parsed.error.flatten(), "The API returned a response this app could not read."));
        return;
      }
      resolve(parsed.data);
    };
    xhr.onerror = () => reject(new ApiError(0, null, "Could not reach the backend."));
    const data = new FormData();
    data.append("file", file);
    xhr.send(data);
  });
}

export { API_URL };
