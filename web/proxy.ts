import { NextResponse, type NextRequest } from "next/server";

/**
 * A pre-filter, NOT the authorization.
 *
 * Named `proxy` and not `middleware`: Next 16 deprecated the middleware file
 * convention in favour of this one, and building emitted a deprecation warning.
 *
 * It does exactly one thing: if there is no session cookie at all and the path is
 * not public, redirect to /login. That removes the flicker where an
 * unauthenticated visitor renders a page shell before the page's own `whoami()`
 * redirects them.
 *
 * What it deliberately does NOT do:
 *
 * 1. **It does not call the API.** Middleware runs on every matched request; a
 *    `/me` round trip per request would be a cost with no benefit, since every
 *    page already calls `whoami()`.
 * 2. **It does not decide roles, and it is not the gate.** Next.js middleware had a
 *    documented bypass class (CVE-2025-29927, the `x-middleware-subrequest`
 *    header), so middleware must never be the only thing between a visitor and a
 *    page. The presence of a cookie is not evidence that the cookie is VALID —
 *    the API decides that on every call, and a forged or expired one gets a 401
 *    from `service/auth.py` exactly as before.
 * 3. **It does not enforce the must-change-password gate.** It cannot know without
 *    an API call. That lives in the pages, backed by the API's 403 with
 *    `code: "password_change_required"`.
 */
const PUBLIC = new Set(["/login"]);

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC.has(pathname)) return NextResponse.next();
  if (request.cookies.has("recon_session")) return NextResponse.next();

  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
