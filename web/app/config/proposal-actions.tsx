"use client";

import { useState, useTransition } from "react";

import { applyProposal, decideProposal, withdrawProposal } from "../actions";

/**
 * Approve, reject, apply, withdraw.
 *
 * Approve and apply are two buttons, not one. Approving says "these numbers are
 * right"; applying writes the file the pipeline reads and commits it. Collapsing
 * them would mean the moment of agreement and the moment of change are the same
 * event, and there would be nowhere to stop between "yes" and "done".
 *
 * The self-approval guard is enforced by the API, not here. Hiding the button is
 * a courtesy; `service/config_store.py::ApprovalPolicy` is the control.
 */
export function ProposalActions({
  id,
  state,
  canDecide,
  isAuthor,
}: {
  id: number;
  state: string;
  canDecide: boolean;
  isAuthor: boolean;
}) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, start] = useTransition();

  const run = (fn: () => Promise<void>) => {
    setError(null);
    start(async () => {
      try {
        await fn();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    });
  };

  return (
    <>
      {error && <div className="notice bad small">{error}</div>}

      {canDecide && state === "pending" && (
        <div style={{ marginBottom: 10 }}>
          <label htmlFor={`note-${id}`}>Decision note</label>
          <input
            id={`note-${id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="confirmed with finance"
            style={{ width: "100%" }}
          />
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {canDecide && state === "pending" && (
          <>
            <button
              disabled={busy}
              onClick={() => run(() => decideProposal(id, "approve", note))}
              title={
                isAuthor
                  ? "If this deployment requires a separate approver, the API will refuse this"
                  : undefined
              }
            >
              Approve
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => run(() => decideProposal(id, "reject", note))}
            >
              Reject
            </button>
          </>
        )}

        {canDecide && state === "approved" && (
          <button disabled={busy} onClick={() => run(() => applyProposal(id))}>
            Apply &amp; commit
          </button>
        )}

        {state === "pending" && (
          <button
            className="danger"
            disabled={busy}
            onClick={() => run(() => withdrawProposal(id))}
          >
            Withdraw
          </button>
        )}
      </div>
    </>
  );
}
