"use client";

import { useActionState } from "react";

import { signIn, type ActionResult } from "../actions";

/**
 * Sign in with a username and password.
 *
 * Entra ID SSO is still the destination and is still blocked on a tenant app
 * registration that needs directory permissions (docs/13-ENTRA-SETUP.md). When it
 * lands, this page becomes a redirect and nothing else in the app changes — which
 * is MORE true now than it was under token paste, because the credential the
 * browser holds is already an opaque server-side session rather than the identity
 * itself.
 *
 * Both fields live in one <form> so a password manager saves them as a pair.
 */
export default function LoginPage() {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(signIn, null);

  return (
    <>
      <h1>Sign in</h1>
      <p className="lede">
        Your session is stored in an httpOnly cookie and is never readable by JavaScript.
      </p>

      <div className="panel" style={{ maxWidth: 620 }}>
        {state && !state.ok && <div className="notice bad">{state.message}</div>}

        <form action={action}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            required
            style={{ width: "100%", marginBottom: 12 }}
          />

          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            style={{ width: "100%", marginBottom: 12 }}
          />

          <button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>

      <p className="muted small" style={{ maxWidth: 620 }}>
        No account? They are created by an admin, or on the server for the very first one —{" "}
        <span className="mono">python -m service.admin user create</span>. Creating the first
        identity needs database access, which is more access than using one; that is deliberate.
      </p>
    </>
  );
}
