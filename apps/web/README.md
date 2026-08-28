# Decision.ai web (Milestone 1)

Internal UI for the decision layer. Pages live in `app/`; API access goes through
`lib/application` hooks and `lib/infrastructure/api-client.ts`. There are no Next.js
API routes and no frontend database.

Requires the FastAPI server on port **8001**. The browser origin is port **3001**.
Full setup is in the repository root `README.md`.

```bash
cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8001
npm install
npm run dev                  # or from repo root: make web
```

Open [http://localhost:3001](http://localhost:3001).
