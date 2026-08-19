"use client";

import { useActionState } from "react";

import { signIn, type ActionResult } from "../actions";
import type { Lang } from "@/lib/words";

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
 *
 * **Two things were rewritten in Phase 5 (B6).** The page used to describe its own
 * cookie as "httpOnly … never readable by JavaScript" — true, a good property, and
 * not something anyone signing in needs to evaluate. And the footer told a person
 * with no account to run `python -m service.admin user create`, which is a command
 * they cannot run, on a machine they do not have, and which asks them to read the
 * one instruction on the page most likely to be beyond them. Both said the right
 * thing to the wrong reader.
 */
export function LoginForm({ lang }: { lang: Lang }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(signIn, null);
  const vi = lang === "vi";

  return (
    <>
      <h1>{vi ? "Đăng nhập" : "Sign in"}</h1>
      <p className="lede">
        {vi
          ? "Hệ thống đối soát doanh thu các sàn thương mại điện tử."
          : "Settlement reconciliation for the marketplace platforms."}
      </p>

      <div className="panel" style={{ maxWidth: 620 }}>
        {/* B9: announced, not just displayed. A sign-in error that only appears
            visually is missed by anyone using a screen reader, and this is the one
            error message in the app somebody is guaranteed to hit. */}
        {state && !state.ok && (
          <div className="notice bad" role="alert" aria-live="assertive">
            {state.message}
          </div>
        )}

        <form action={action}>
          <label htmlFor="username">{vi ? "Tên đăng nhập" : "Username"}</label>
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

          <label htmlFor="password">{vi ? "Mật khẩu" : "Password"}</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            style={{ width: "100%", marginBottom: 12 }}
          />

          <button type="submit" disabled={pending}>
            {pending
              ? vi
                ? "Đang đăng nhập…"
                : "Signing in…"
              : vi
                ? "Đăng nhập"
                : "Sign in"}
          </button>
        </form>
      </div>

      <p className="muted small" style={{ maxWidth: 620 }}>
        {vi
          ? "Chưa có tài khoản? Tài khoản do quản trị viên tạo — hãy liên hệ người phụ trách hệ thống này."
          : "No account? Accounts are created by an administrator — ask whoever looks after this system."}
      </p>
    </>
  );
}
