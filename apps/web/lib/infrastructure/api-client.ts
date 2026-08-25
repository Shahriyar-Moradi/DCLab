import type { ZodType } from "zod";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
      ...init?.headers,
    },
  });
  const body = await parseJson(response);
  if (!response.ok) {
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

export function apiPost<T>(path: string, schema: ZodType<T>, json: unknown): Promise<T> {
  return request(path, schema, { method: "POST", body: JSON.stringify(json) });
}

export function uploadFile<T>(
  path: string,
  schema: ZodType<T>,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildUrl(path));
    xhr.responseType = "text";
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
