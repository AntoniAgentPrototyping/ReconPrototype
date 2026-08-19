"use client";

import { useActionState } from "react";

import { createUser, type ActionResult } from "../../actions";

/**
 * No password field, deliberately — see the note on the page.
 *
 * The generated password comes back in the success message, which necessarily puts
 * it in the DOM. That is unavoidable if an admin is to hand it over; what matters
 * is that it never goes into a URL or a redirect, where it would land in browser
 * history and in any proxy log.
 */
export default function CreateUserForm() {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    createUser,
    null,
  );

  return (
    <div className="panel" style={{ maxWidth: 620 }}>
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`} aria-live="polite">
          {state.message}
          {state.ok && (
            <div className="muted small" style={{ marginTop: 6 }}>
              Copy it now — only its hash is stored, so it cannot be shown again.
            </div>
          )}
        </div>
      )}

      <form action={action}>
        <label htmlFor="username">Username</label>
        <input
          id="username"
          name="username"
          type="text"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="someone@ada"
          required
          minLength={3}
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label htmlFor="display_name">Display name (optional)</label>
        <input
          id="display_name"
          name="display_name"
          type="text"
          autoComplete="off"
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label htmlFor="role">Role</label>
        <select id="role" name="role" defaultValue="recon.user" style={{ marginBottom: 12 }}>
          <option value="recon.viewer">recon.viewer — read only</option>
          <option value="recon.user">recon.user — upload, run, request changes</option>
          <option value="recon.admin">recon.admin — also approve and manage accounts</option>
        </select>

        <div>
          <button type="submit" disabled={pending}>
            {pending ? "Creating…" : "Create account"}
          </button>
        </div>
      </form>
    </div>
  );
}
