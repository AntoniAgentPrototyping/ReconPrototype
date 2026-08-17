import { redirect } from "next/navigation";

import { whoami } from "@/lib/api";

import PasswordForm from "./password-form";

export const dynamic = "force-dynamic";

/**
 * Change your own password.
 *
 * Reachable while `must_change_password` is set — it and `/me` are the only routes
 * the API allows in that state, because a password the admin generated is a
 * credential somebody other than its owner knows.
 */
export default async function PasswordPage() {
  const me = await whoami();
  if (!me) redirect("/login");

  return (
    <>
      <h1>Password</h1>
      {me.must_change_password ? (
        <div className="notice bad" style={{ maxWidth: 620 }}>
          Your password was set by someone else — an admin, or the server during setup. Until
          you change it, everything except this page is refused. That is deliberate: a
          credential another person has seen should not keep working.
        </div>
      ) : (
        <p className="lede">
          Changing your password signs out every other session you have open. This one stays.
        </p>
      )}

      <PasswordForm />

      <p className="muted small" style={{ maxWidth: 620 }}>
        At least 12 characters. Length and a blocklist, no composition rules — those produce{" "}
        <span className="mono">Password1!</span>. Your current password is required even when
        you are being forced to change it, so whoever holds this browser session cannot lock
        you out without also knowing it.
      </p>
    </>
  );
}
