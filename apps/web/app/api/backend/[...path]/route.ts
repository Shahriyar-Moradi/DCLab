import { NextRequest } from "next/server";
import { proxyBackend } from "@/lib/infrastructure/bff-proxy";

type RouteCtx = { params: Promise<{ path: string[] }> };

export const runtime = "nodejs";

async function handle(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxyBackend(req, path);
}

export const GET = handle;
export const HEAD = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
