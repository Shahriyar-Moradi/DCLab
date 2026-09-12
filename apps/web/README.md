# Decision.ai web (Milestone 1)

Internal UI for the decision layer. Pages live in `app/`; API access goes through
`lib/application` hooks and `lib/infrastructure/api-client.ts`. Browser requests use
the same-origin Next.js BFF at `/api/backend/*` (`lib/infrastructure/bff-proxy.ts`);
only that server-side route talks to FastAPI. The BFF copies session/CSRF cookies,
forwards `X-Request-Id` (generating one when missing), streams with upload/download
byte bounds, sets `Cache-Control: no-store`, and returns
`{"detail":"backend unavailable"}` with HTTP 502 when FastAPI cannot be reached.
It does not parse bearer tokens, authorize roles, or log bodies.

There is no frontend database, and browser JavaScript does not read or attach
the opaque HttpOnly session cookie. Navigation may hide links using `/auth/me`
role for presentation; the API remains the authorization authority.

Requires the FastAPI server on port **8001**. The browser origin is port **3001**.
Full setup is in the repository root `README.md`.

```bash
cp .env.example .env.local   # DCLAB_API_URL=http://localhost:8001
npm ci
npm run dev                  # or from repo root: make web
```

Open [http://localhost:3001](http://localhost:3001).
