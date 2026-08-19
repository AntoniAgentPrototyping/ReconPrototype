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
/** What each confirmation actually costs the person it is done to. */
const CONFIRMATIONS: Record<string, (username: string) => string> = {
  reset: (u) => `Reset ${u}'s password? Their current one stops working immediately and they need the temporary one from you to get back in.`,
  revoke: (u) => `Sign ${u} out of every browser? Anything they have open stops working and they sign in again.`,
  disable: (u) => `Disable ${u}? They cannot sign in at all until somebody re-enables the account.`,
};

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
  // B8: a role change is applied by a button, not by the select. `onChange`
  // committed a privilege change on a keystroke — arrowing through the list to
  // read the options granted admin on the way past, and a mis-click was
  // indistinguishable from a decision. `chosen` holds the intent; nothing leaves
  // the browser until Apply.
  const [chosen, setChosen] = useState(role);
  // B8: the three irreversible-ish actions ask first. Reset password invalidates
  // the person's current one, and sign-out-everywhere ends live sessions —
  // recoverable, but not by them and not quickly, and both were one stray click.
  const [confirming, setConfirming] = useState<string | null>(null);

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
          onClick={() => setConfirming("reset")}
        >
          Reset password
        </button>

        <button
          type="button"
          disabled={pending}
          onClick={() => setConfirming("revoke")}
          title="Sign this person out of every browser"
        >
          Sign out everywhere
        </button>

        {!protectedRow && (
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              // Enabling somebody is not destructive; disabling them is.
              disabled ? run(() => setUserDisabled(userId, false)) : setConfirming("disable")
            }
          >
            {disabled ? "Enable" : "Disable"}
          </button>
        )}

        {!protectedRow && (
          <>
            <select
              value={chosen}
              disabled={pending}
              onChange={(e) => setChosen(e.target.value)}
              aria-label={`Role for ${username}`}
            >
              <option value="recon.viewer">recon.viewer</option>
              <option value="recon.user">recon.user</option>
              <option value="recon.admin">recon.admin</option>
            </select>
            {chosen !== role && (
              <button
                type="button"
                disabled={pending}
                onClick={() => run(() => setUserRole(userId, chosen))}
              >
                Apply {chosen.replace("recon.", "")}
              </button>
            )}
          </>
        )}
      </div>

      {confirming && (
        <div className="notice" style={{ marginTop: 6 }}>
          <span className="small">{CONFIRMATIONS[confirming](username)}</span>{" "}
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              const action = confirming;
              setConfirming(null);
              run(() =>
                action === "reset"
                  ? resetUserPassword(userId)
                  : action === "revoke"
                    ? revokeUserSessions(userId)
                    : setUserDisabled(userId, true),
              );
            }}
          >
            Yes
          </button>{" "}
          <button type="button" className="secondary" onClick={() => setConfirming(null)}>
            No
          </button>
        </div>
      )}

      {protectedRow && (
        <div className="muted small">
          {isSelf ? "your own account" : "the only admin"}
        </div>
      )}

      {result && (
        <div
          className={`notice ${result.ok ? "good" : "bad"}`}
          style={{ marginTop: 6 }}
          aria-live="polite"
        >
          {result.message}
        </div>
      )}
    </div>
  );
}
