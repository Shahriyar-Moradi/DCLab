/**
 * Typed /v1 helpers over the existing `apiGet` / `apiPost` / `apiPostForm` /
 * `apiDownload` transport (BFF, session cookie, CSRF, workspace header).
 *
 * Types come from `schema.d.ts`, generated from the live FastAPI OpenAPI by
 * `npm run gen:api`; a backend contract change regenerates it and any call
 * whose path, path parameters, body or response view no longer fits fails `tsc`.
 * Runtime validation stays with the caller's zod schema, as everywhere else.
 */
import type { ZodType } from "zod";
import { apiDownload, apiGet, apiPost, apiPostForm, newIdempotencyKey } from "../api-client";
import type { components, paths } from "./schema";

export { newIdempotencyKey };

export type V1Paths = paths;
export type V1SchemaName = keyof components["schemas"];
/** A named component schema of the /v1 API, e.g. `V1Schema<"ServiceTokenRead">`. */
export type V1Schema<N extends V1SchemaName> = components["schemas"][N];

export type V1Method = "get" | "post" | "put" | "patch" | "delete";
/** Every /v1 path template that has an operation for `M`. */
export type V1Path<M extends V1Method> = {
  [P in keyof paths]: [NonNullable<paths[P][M]>] extends [never] ? never : P;
}[keyof paths];
export type V1Operation<P extends V1Path<M>, M extends V1Method> = NonNullable<paths[P][M]>;

type SuccessCode = 200 | 201 | 202;
type JsonContent<R> = R extends { content: { "application/json": infer Body } } ? Body : never;
type Responses<P extends V1Path<M>, M extends V1Method> = V1Operation<P, M> extends { responses: infer R } ? R : never;
/** The JSON body of a 2xx response. */
export type V1Response<P extends V1Path<M>, M extends V1Method> = {
  [C in keyof Responses<P, M> & SuccessCode]: JsonContent<Responses<P, M>[C]>;
}[keyof Responses<P, M> & SuccessCode];
/** The JSON request body (`Record<string, never>` when the operation takes none). */
export type V1RequestBody<P extends V1Path<M>, M extends V1Method> =
  V1Operation<P, M> extends { requestBody: { content: { "application/json": infer Body } } }
    ? Body
    : V1Operation<P, M> extends { requestBody?: { content: { "application/json": infer Body } } }
      ? Body | undefined
      : Record<string, never>;
export type V1Query<P extends V1Path<M>, M extends V1Method> =
  V1Operation<P, M> extends { parameters: { query?: infer Q } } ? ([Q] extends [never] ? never : NonNullable<Q>) : never;

/** `"/v1/a/{x}/b/{y}"` → `"x" | "y"`. */
export type V1PathParamNames<P extends string> = P extends `${string}{${infer Name}}${infer Rest}`
  ? Name | V1PathParamNames<Rest>
  : never;
export type V1PathParams<P extends string> = { [K in V1PathParamNames<P>]: string };
type PathArg<P extends string> = [V1PathParamNames<P>] extends [never]
  ? { params?: undefined }
  : { params: V1PathParams<P> };
/** Options are required exactly when the path template has parameters. */
type OptionsArg<P extends string, Extra> = [V1PathParamNames<P>] extends [never]
  ? [options?: PathArg<P> & Extra]
  : [options: PathArg<P> & Extra];

/**
 * A UI zod schema may read a subset of the response: every API value must fit
 * the UI type, and the UI may not read a key the API does not send.
 */
type ElementOf<T> = T extends readonly (infer E)[] ? E : T;
type UnknownKeys<View, Api> = Exclude<keyof ElementOf<View>, keyof ElementOf<Api>>;
export type ReadableAs<Api, View> = [Api] extends [View]
  ? [UnknownKeys<View, Api>] extends [never]
    ? View
    : { __uiReadsKeysTheApiDoesNotSend: UnknownKeys<View, Api> }
  : { __apiResponseDoesNotFitUiSchema: Api };

/** Fill a path template; every value is URI-encoded. */
export function v1Path<P extends string>(template: P, params?: V1PathParams<P>): string {
  return template.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = (params as Record<string, string> | undefined)?.[name];
    if (value === undefined) throw new Error(`Missing path parameter "${name}" for ${template}`);
    return encodeURIComponent(value);
  });
}

/** Headers for a /v1 command: one `Idempotency-Key` per user action (retries reuse it). */
export function withIdempotency(key: string = newIdempotencyKey(), headers: Record<string, string> = {}): Record<string, string> {
  return { ...headers, "Idempotency-Key": key };
}

/**
 * `If-Match` for an optimistic-concurrency mutation. Pass the `etag` the API
 * returned (already a quoted strong tag), a ref `version` number, or `"*"`.
 */
export function ifMatch(etag: string | number, headers: Record<string, string> = {}): Record<string, string> {
  let value: string;
  if (typeof etag === "number") {
    value = `"${etag}"`;
  } else if (etag.startsWith("W/")) {
    throw new Error("If-Match needs a strong ETag; the /v1 API never matches weak tags.");
  } else {
    value = etag === "*" || etag.startsWith('"') ? etag : `"${etag}"`;
  }
  return { ...headers, "If-Match": value };
}

type QueryArg = Record<string, string | number | boolean | null | undefined>;

function queryParams(query?: QueryArg): Record<string, string | number | boolean | undefined> | undefined {
  if (!query) return undefined;
  const out: Record<string, string | number | boolean | undefined> = {};
  for (const [key, value] of Object.entries(query)) {
    if (value !== null) out[key] = value;
  }
  return out;
}

export function v1Get<P extends V1Path<"get">, T>(
  path: P,
  schema: ZodType<T> & ZodType<ReadableAs<V1Response<P, "get">, T>>,
  ...[options]: OptionsArg<P, { query?: V1Query<P, "get"> & QueryArg }>
): Promise<T> {
  return apiGet(v1Path(path, options?.params), schema, queryParams(options?.query));
}

type CommandOptions = {
  /** Defaults to a fresh key; pass the action's key so a retried submit replays. */
  idempotencyKey?: string;
  headers?: Record<string, string>;
};

export function v1Post<P extends V1Path<"post">, T>(
  path: P,
  schema: ZodType<T> & ZodType<ReadableAs<V1Response<P, "post">, T>>,
  body: V1RequestBody<P, "post">,
  ...[options]: OptionsArg<P, CommandOptions>
): Promise<T> {
  return apiPost(v1Path(path, options?.params), schema, body ?? {}, withIdempotency(options?.idempotencyKey, options?.headers));
}

export function v1PostForm<P extends V1Path<"post">, T>(
  path: P,
  schema: ZodType<T> & ZodType<ReadableAs<V1Response<P, "post">, T>>,
  form: FormData,
  ...[options]: OptionsArg<P, CommandOptions>
): Promise<T> {
  return apiPostForm(v1Path(path, options?.params), schema, form, withIdempotency(options?.idempotencyKey, options?.headers));
}

export function v1Download<P extends V1Path<"get">>(
  path: P,
  ...[options]: OptionsArg<P, { accept?: string; fallbackFilename?: string }>
): Promise<{ blob: Blob; filename: string }> {
  return apiDownload(v1Path(path, options?.params), { accept: options?.accept, fallbackFilename: options?.fallbackFilename });
}
