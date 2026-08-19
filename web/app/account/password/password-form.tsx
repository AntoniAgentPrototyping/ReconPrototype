"use client";

import { useActionState } from "react";

import { changeOwnPassword, type ActionResult } from "../../actions";

/**
 * The confirm-match check here is a courtesy. The API is the control — it
 * re-verifies the current password, applies the length and blocklist policy, and
 * refuses a new password equal to the old one.
 */
export default function PasswordForm() {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    changeOwnPassword,
    null,
  );

  return (
    <div className="panel" style={{ maxWidth: 620 }}>
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`} aria-live="polite">
          {state.message}
        </div>
      )}

      <form action={action}>
        <label htmlFor="current_password">Current password</label>
        <input
          id="current_password"
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label htmlFor="new_password">New password</label>
        <input
          id="new_password"
          name="new_password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label htmlFor="confirm_password">New password again</label>
        <input
          id="confirm_password"
          name="confirm_password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
          style={{ width: "100%", marginBottom: 12 }}
        />

        <button type="submit" disabled={pending}>
          {pending ? "Changing…" : "Change password"}
        </button>
      </form>
    </div>
  );
}
