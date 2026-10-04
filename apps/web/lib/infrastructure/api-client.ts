import type { ZodType } from "zod";
import {
  getActiveWorkspaceId,
  getWorkspaceRequestSignal,
  REQUEST_ID_HEADER,
  WORKSPACE_HEADER,
} from "./active-workspace";
import { CSRF_HEADER, ensureCsrfToken } from "./csrf";
import { notifySessionChanged } from "./session";

const SESSION_AUTH_PATHS = new Set([
  "/auth/login",
  "/auth/me",
  "/auth/register",
  "/auth/tokens",
]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
function usesWorkspaceContext(path: string): boolean {
  return path !== "/health" && !path.startsWith("/auth/");
}

type RequestWorkspace = { id: string | null; signal?: AbortSignal };

function requestWorkspace(path: string): RequestWorkspace {
  return usesWorkspaceContext(path)
    ? { id: getActiveWorkspaceId(), signal: getWorkspaceRequestSignal() }
    : { id: null };
}

function workspaceRequestSignal(context: RequestWorkspace, explicit?: AbortSignal | null): AbortSignal | undefined {
  if (explicit && context.signal) return AbortSignal.any([explicit, context.signal]);
  return explicit ?? context.signal;
}

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

function contractHeaders(path: string, context: RequestWorkspace, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (!headers.has(REQUEST_ID_HEADER)) {
    headers.set(REQUEST_ID_HEADER, newRequestId());
  }
  const workspaceId = context.id;
  if (workspaceId && usesWorkspaceContext(path) && !headers.has(WORKSPACE_HEADER)) {
    headers.set(WORKSPACE_HEADER, workspaceId);
  }
  return headers;
}

async function csrfHeaders(path: string, context: RequestWorkspace, existing?: HeadersInit): Promise<Headers> {
  const token = await ensureCsrfToken(apiRoot());
  const headers = contractHeaders(path, context, existing);
  headers.set(CSRF_HEADER, token);
  return headers;
}

function mergeRequestHeaders(path: string, method: string, context: RequestWorkspace, init?: RequestInit): Promise<Headers> | Headers {
  const base = contractHeaders(path, context, {
    Accept: "application/json",
    ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...init?.headers,
  });
  if (SAFE_METHODS.has(method)) {
    return base;
  }
  return csrfHeaders(path, context, base);
}

/** Legacy ``{detail}`` bodies and the /v1 ``{error: {message}}`` envelope. */
function errorMessage(body: unknown): string | null {
  if (typeof body !== "object" || !body) return null;
  if ("detail" in body) return String((body as { detail: unknown }).detail);
  const error = (body as { error?: { message?: unknown } }).error;
  return error && typeof error.message === "string" ? error.message : null;
}

async function request<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const context = requestWorkspace(path);
  const headers = await mergeRequestHeaders(path, method, context, init);
  const signal = workspaceRequestSignal(context, init?.signal);
  signal?.throwIfAborted();
  const response = await fetch(buildUrl(path, params), {
    ...init,
    credentials: "include",
    headers,
    signal,
  });
  const body = await parseJson(response);
  if (!response.ok) {
    dropSessionOnUnauthorized(path, response.status);
    throw new ApiError(response.status, body, errorMessage(body) || response.statusText || `Request failed (${response.status})`);
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
  const context = requestWorkspace(path);
  const headers = contractHeaders(path, context, {
    Accept: options?.accept ?? "*/*",
  });
  const response = await fetch(buildUrl(path), {
    credentials: "include",
    headers,
    signal: context.signal,
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

export function apiPost<T>(
  path: string,
  schema: ZodType<T>,
  json: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  return request(path, schema, { method: "POST", body: JSON.stringify(json), headers });
}

/** A fresh /v1 Idempotency-Key for one user action (retries of that action reuse it). */
export function newIdempotencyKey(): string {
  return `studio-${newRequestId()}`;
}

export function apiPut<T>(path: string, schema: ZodType<T>, json: unknown): Promise<T> {
  return request(path, schema, { method: "PUT", body: JSON.stringify(json) });
}

export async function apiPostEmpty(path: string): Promise<void> {
  const context = requestWorkspace(path);
  const headers = await csrfHeaders(path, context, { Accept: "application/json" });
  context.signal?.throwIfAborted();
  const response = await fetch(buildUrl(path), {
    method: "POST",
    credentials: "include",
    headers,
    signal: context.signal,
  });
  if (!response.ok) {
    dropSessionOnUnauthorized(path, response.status);
    throw new ApiError(response.status, null, response.statusText || `Request failed (${response.status})`);
  }
}

export function apiPostForm<T>(
  path: string,
  schema: ZodType<T>,
  form: FormData,
  headers?: Record<string, string>,
): Promise<T> {
  return request(path, schema, { method: "POST", body: form, headers });
}

export function uploadFile<T>(
  path: string,
  schema: ZodType<T>,
  file: File,
  onProgress?: (percent: number) => void,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const context = requestWorkspace(path);
  return new Promise((resolve, reject) => {
    void (async () => {
      let headers: Headers;
      try {
        headers = await csrfHeaders(path, context);
      } catch (error) {
        reject(error);
        return;
      }
      const xhr = new XMLHttpRequest();
      const workspaceSignal = context.signal;
      const abortUpload = () => xhr.abort();
      const cleanupAbortListener = () => workspaceSignal?.removeEventListener("abort", abortUpload);
      if (workspaceSignal?.aborted) {
        reject(new DOMException("Workspace changed", "AbortError"));
        return;
      }
      workspaceSignal?.addEventListener("abort", abortUpload, { once: true });
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
        cleanupAbortListener();
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
      xhr.onerror = () => {
        cleanupAbortListener();
        reject(new ApiError(0, null, "Could not reach the backend."));
      };
      xhr.onabort = () => {
        cleanupAbortListener();
        reject(new DOMException("Workspace changed", "AbortError"));
      };
      const data = new FormData();
      data.append("file", file);
      xhr.send(data);
    })();
  });
}

export { apiRoot };
