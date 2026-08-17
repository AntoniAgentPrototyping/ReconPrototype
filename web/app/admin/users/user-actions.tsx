"use client";

import { useState, useTransition } from "react";

import {
  resetUserPassword,
  revokeUserSessions,
  setUserDisabled,
  setUserRole,
  type ActionResult,
} from "../../actions";

/**
 * Per-row account actions.
 *
 * Disable and demote are hidden for yourself and for the last remaining admin.
 * That is a courtesy — the API refuses both with a 409 regardless, because a
 * deployment whose only admin has locked themselves out cannot be administered
 * from the browser at all and needs someone with the database URL.
 */
export default function UserActions({
  userId,
  username,
  role,
  disabled,
  isSelf,
  isLastAdmin,
}: {
  userId: number;
  username: string;
  role: string;
  disabled: boolean;
  isSelf: boolean;
  isLastAdmin: boolean;
}) {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  function run(fn: () => Promise<ActionResult>) {
    start(async () => setResult(await fn()));
  }

  const protectedRow = isSelf || isLastAdmin;

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <button
          type="button"
          disabled={pending}
          onClick={() => run(() => resetUserPassword(userId))}
        >
          Reset password
        </button>

        <button
          type="button"
          disabled={pending}
          onClick={() => run(() => revokeUserSessions(userId))}
          title="Sign this person out of every browser"
        >
          Sign out everywhere
        </button>

        {!protectedRow && (
          <button
            type="button"
            disabled={pending}
            onClick={() => run(() => setUserDisabled(userId, !disabled))}
          >
            {disabled ? "Enable" : "Disable"}
          </button>
        )}

        {!protectedRow && (
          <select
            defaultValue={role}
            disabled={pending}
            onChange={(e) => run(() => setUserRole(userId, e.target.value))}
            aria-label={`Role for ${username}`}
          >
            <option value="recon.viewer">recon.viewer</option>
            <option value="recon.user">recon.user</option>
            <option value="recon.admin">recon.admin</option>
          </select>
        )}
      </div>

      {protectedRow && (
        <div className="muted small">
          {isSelf ? "your own account" : "the only admin"}
        </div>
      )}

      {result && (
        <div className={`notice ${result.ok ? "good" : "bad"}`} style={{ marginTop: 6 }}>
          {result.message}
        </div>
      )}
    </div>
  );
}
