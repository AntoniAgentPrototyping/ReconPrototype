/**
 * `standalone` output so the runtime image carries only the traced dependencies
 * — roughly 150 MB instead of ~1 GB with the whole node_modules tree.
 *
 * Note what is NOT here: no `NEXT_PUBLIC_API_URL`. Baking the API address in at
 * build time is the classic Next-in-Docker trap — it produces an image that only
 * works in the environment it was built for, which defeats promoting one image
 * from local to Railway. The API address is read from `process.env` at request
 * time, on the server, in lib/api.ts.
 *
 * CSRF, and the one thing to remember when this gets a real hostname:
 *
 * The API reads only the `Authorization` header and never a cookie, so CSRF against
 * the API is structurally absent. It does exist HERE, because a server action is a
 * browser POST with the session cookie attached. Three things cover it: the cookie
 * is `sameSite: "lax"`, so it does not ride a cross-site POST; Next checks a server
 * action's `Origin` against its `Host`; and action ids are unguessable.
 *
 * **Behind a reverse proxy that rewrites `Host`, the second of those is exactly what
 * breaks.** Since Phase 6 (C4) the fix is wired rather than a comment: build with
 *
 *     RECON_ALLOWED_ORIGINS=recon.example.com   (comma-separated for several)
 *
 * and `serverActions.allowedOrigins` is set from it. A BUILD-time value, because
 * this file is serialized into the standalone output — an image built for a
 * hostname is built FOR it (deploy/docker-compose.yml passes it as a build arg).
 * Unset means unset: localhost needs none, and a wrong value here fails every
 * mutation with an opaque error.
 */
const allowedOrigins = (process.env.RECON_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  ...(allowedOrigins.length
    ? { experimental: { serverActions: { allowedOrigins } } }
    : {}),
};

export default nextConfig;
