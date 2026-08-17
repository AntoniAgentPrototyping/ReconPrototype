import { NextResponse } from "next/server";

import { apiBase, currentSession } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Stream an artifact through the BFF.
 *
 * `fetch` rather than `api()` because the body is a workbook, not JSON — and it
 * is piped straight through rather than buffered, so a 40 MB Shopee finance file
 * does not sit in this process's memory on its way to the browser.
 *
 * Residual, noted rather than fixed: this is a GET that carries the session cookie,
 * and `sameSite: "lax"` does not block a top-level cross-site navigation. It is
 * read-only, so the exposure is "another site can cause a download the user is
 * already entitled to", not a write. `sameSite: "strict"` would close it and breaks
 * nothing today — but it also breaks the OIDC redirect-back pattern Entra will need,
 * so `lax` stays and the reason is recorded here rather than rediscovered later.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string; name: string }> },
) {
  const { id, name } = await params;
  const token = await currentSession();
  if (!token) return NextResponse.json({ detail: "not signed in" }, { status: 401 });

  // Was a second inline copy of apiBase(); a change to that one silently
  // missed this one. Now there is one implementation.
  const base = apiBase();
  const upstream = await fetch(
    `${base}/runs/${Number(id)}/artifacts/${encodeURIComponent(name)}`,
    { headers: { authorization: `Bearer ${token}` }, cache: "no-store" },
  );

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => upstream.statusText);
    return NextResponse.json({ detail }, { status: upstream.status });
  }

  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type":
        upstream.headers.get("content-type") ?? "application/octet-stream",
      // The filename is server-controlled and already validated on upload; quote
      // it so a name with a space survives the header.
      "content-disposition": `attachment; filename="${name.replace(/"/g, "")}"`,
      "cache-control": "no-store",
    },
  });
}
