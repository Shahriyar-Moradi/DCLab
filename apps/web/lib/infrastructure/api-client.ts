import type { ZodType } from "zod";
import {
  getActiveWorkspaceId,
  REQUEST_ID_HEADER,
  WORKSPACE_HEADER,
} from "./active-workspace";
import { CSRF_HEADER, ensureCsrfToken } from "./csrf";
import { notifySessionChanged } from "./session";

const SESSION_AUTH_PATHS = new Set(["/auth/login", "/auth/register", "/auth/tokens"]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const SKIP_WORKSPACE_HEADER = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/tokens",
  "/auth/csrf",
  "/health",
]);

function apiRoot(): string {
  if (typeof window !== "undefined") {
    return `${window.location.origin}/api/backend`;
  }
  const origin =
    process.env.DCLAB_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
  return origin.replace(/\/$/, "");
}

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
  const url = new URL(`${apiRoot()}${path.startsWith("/") ? path : `/${path}`}`);
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

function dropSessionOnUnauthorized(path: string, status: number): void {
  if (status !== 401) return;
  if (SESSION_AUTH_PATHS.has(path)) return;
  notifySessionChanged();
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function contractHeaders(path: string, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (!headers.has(REQUEST_ID_HEADER)) {
    headers.set(REQUEST_ID_HEADER, newRequestId());
  }
  const workspaceId = getActiveWorkspaceId();
  if (workspaceId && !SKIP_WORKSPACE_HEADER.has(path) && !headers.has(WORKSPACE_HEADER)) {
    headers.set(WORKSPACE_HEADER, workspaceId);
  }
  return headers;
}

async function csrfHeaders(path: string, existing?: HeadersInit): Promise<Headers> {
  const token = await ensureCsrfToken(apiRoot());
  const headers = contractHeaders(path, existing);
  headers.set(CSRF_HEADER, token);
  return headers;
}

function mergeRequestHeaders(path: string, method: string, init?: RequestInit): Promise<Headers> | Headers {
  const base = contractHeaders(path, {
    Accept: "application/json",
    ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...init?.headers,
  });
  if (SAFE_METHODS.has(method)) {
    return base;
  }
  return csrfHeaders(path, base);
}

async function request<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const headers = await mergeRequestHeaders(path, method, init);
  const response = await fetch(buildUrl(path, params), {
    ...init,
    credentials: "include",
    headers,
  });
  const body = await parseJson(response);
  if (!response.ok) {
    dropSessionOnUnauthorized(path, response.status);
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

export async function apiDownload(
  path: string,
  options?: { accept?: string; fallbackFilename?: string },
): Promise<{ blob: Blob; filename: string }> {
  const headers = contractHeaders(path, {
    Accept: options?.accept ?? "*/*",
  });
  const response = await fetch(buildUrl(path), {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    dropSessionOnUnauthorized(path, response.status);
    throw new ApiError(response.status, null, response.statusText || `Request failed (${response.status})`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const quoted = /filename="([^"]+)"/i.exec(disposition);
  const unquoted = /filename=([^;]+)/i.exec(disposition);
  const filename = (quoted?.[1] || unquoted?.[1] || "").trim();
  return {
    blob: await response.blob(),
    filename: filename || options?.fallbackFilename || "download",
  };
}

export function apiPost<T>(path: string, schema: ZodType<T>, json: unknown): Promise<T> {
  return request(path, schema, { method: "POST", body: JSON.stringify(json) });
}

export function apiPut<T>(path: string, schema: ZodType<T>, json: unknown): Promise<T> {
  return request(path, schema, { method: "PUT", body: JSON.stringify(json) });
}

export async function apiPostEmpty(path: string): Promise<void> {
  const response = await fetch(buildUrl(path), {
    method: "POST",
    credentials: "include",
    headers: await csrfHeaders(path, { Accept: "application/json" }),
  });
  if (!response.ok) {
    dropSessionOnUnauthorized(path, response.status);
    throw new ApiError(response.status, null, response.statusText || `Request failed (${response.status})`);
  }
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
    void (async () => {
      let headers: Headers;
      try {
        headers = await csrfHeaders(path);
      } catch (error) {
        reject(error);
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", buildUrl(path, params));
      xhr.withCredentials = true;
      xhr.responseType = "text";
      headers.forEach((value, key) => {
        xhr.setRequestHeader(key, value);
      });
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
          dropSessionOnUnauthorized(path, xhr.status);
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
    })();
  });
}

export { apiRoot };
