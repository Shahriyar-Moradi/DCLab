/**
 * Compile-time drift checks: the UI's /v1 zod schemas against the types
 * generated from the backend OpenAPI (`npm run gen:api`). A backend rename,
 * removal or type change of a field the UI reads fails `npx tsc --noEmit`.
 *
 * Direction: responses — the API type must fit the UI schema's output and the UI
 * may not read a key the API does not send (the UI may read a subset). Requests —
 * what the UI sends must fit the API request body. Add a line here whenever a
 * new /v1 zod schema lands; this file has no runtime code.
 */
import type { z } from "zod";
import type {
  SERVICE_TOKEN_SCOPES,
  ServiceTokenCreatedSchema,
  ServiceTokenSchema,
} from "../../domain/schemas";
import type { ServiceTokenCreateInput } from "../../application/hooks";
import type { ReadableAs, V1RequestBody, V1Response, V1Schema } from "./client";

/** Resolves to `true` only when `Actual` is exactly `true`. */
type Expect<Actual extends true> = Actual;
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
/** The UI schema output `View` can read the API value `Api`. */
type Reads<View, Api> = Equal<ReadableAs<Api, View>, View>;
/** The UI value `Sent` is a valid API request body `Api`. */
type Sends<Sent, Api> = [Sent] extends [Api] ? true : false;

// --- Service tokens (P3.2-A) ----------------------------------------------------
type ServiceToken = z.infer<typeof ServiceTokenSchema>;
type ServiceTokenCreated = z.infer<typeof ServiceTokenCreatedSchema>;

export type ServiceTokenContract = [
  Expect<Reads<ServiceToken, V1Schema<"ServiceTokenRead">>>,
  Expect<Reads<ServiceTokenCreated, V1Schema<"ServiceTokenCreatedRead">>>,
  Expect<Reads<ServiceToken[], V1Response<"/v1/service-tokens", "get">>>,
  Expect<Reads<ServiceTokenCreated, V1Response<"/v1/service-tokens", "post">>>,
  Expect<Reads<ServiceToken, V1Response<"/v1/service-tokens/{token_id}/revoke", "post">>>,
  Expect<Sends<Omit<ServiceTokenCreateInput, "idempotencyKey">, V1RequestBody<"/v1/service-tokens", "post">>>,
  // The scope checkboxes offer exactly the scopes the API accepts.
  Expect<Equal<(typeof SERVICE_TOKEN_SCOPES)[number], V1Schema<"ServiceTokenCreateRequest">["scopes"][number]>>,
];

// --- Self-test: the checks above really reject drift ------------------------------
export type ContractCheckSelfTest = [
  // Backend renamed `prefix` → `token_prefix`: the UI reads a key the API no longer sends.
  Expect<Equal<Reads<{ id: string; prefix: string }, { id: string; token_prefix: string }>, false>>,
  // Backend changed a type, or made a field nullable that the UI treats as always set.
  Expect<Equal<Reads<{ id: string }, { id: number }>, false>>,
  Expect<Equal<Reads<{ expires_at: string }, { expires_at: string | null }>, false>>,
  // Backend added a field: the UI may keep reading a subset.
  Expect<Reads<{ id: string }, { id: string; added: boolean }>>,
  // Backend narrowed an accepted value the UI still sends.
  Expect<Equal<Sends<{ scopes: string[] }, { scopes: "read"[] }>, false>>,
];
