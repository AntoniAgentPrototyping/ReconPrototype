import { NextRequest, NextResponse } from "next/server";

import { ApiError, api, type LogPage } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The BFF endpoint the log poller calls.
 *
 * It exists so the browser can poll without ever holding the bearer token: the
 * token is attached here, on the server, from the httpOnly cookie. This is the
 * concrete reason the reconciliation API needs no public address — only this
 * process talks to it.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const after = request.nextUrl.searchParams.get("after_seq") ?? "-1";

  // Bound it here as well as in the API: a hand-edited query string should not
  // be able to ask this process for an unbounded page.
  const cursor = Number.parseInt(after, 10);
  const safe = Number.isFinite(cursor) && cursor >= -1 ? cursor : -1;

  try {
    const page = await api<LogPage>(`/runs/${Number(id)}/log?after_seq=${safe}&limit=2000`);
    return NextResponse.json(page, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ detail: error.detail }, { status: error.status });
    }
    throw error;
  }
}
