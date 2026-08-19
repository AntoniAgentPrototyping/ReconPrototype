"use client";

import { useState, useTransition } from "react";

import { cancelRun, requeueRun, type ActionResult } from "../../actions";

/**
 * The two things a person wants from a run page and could not do (**B4**).
 *
 * Before this, a finished run offered no way to run the window again and an
 * in-flight one offered no way to stop it — from *this* page. Cancel existed on
 * the board, which means the person watching a run go wrong had to navigate away
 * from what they were watching to act on it.
 *
 * **Re-run is confirmed, and the confirmation is not decoration.** A settlement
 * run writes a finance file the team invoices from, and `max_attempts` defaults
 * to 1 precisely because retrying one is a second write of the same money
 * ([D30](docs/06-DECISIONS.md#d30)). The API's own guard is the real control —
 * one live job per window, 409 otherwise — and this is the layer that stops
 * somebody triggering it by reflex.
 *
 * **Cancel is not.** Stopping something that has not finished is recoverable by
 * definition, and putting a dialog in front of it makes the fast path slower at
 * exactly the moment somebody has noticed a problem.
 */
export function RunActions({
  runId,
  jobId,
  platform,
  period,
  inFlight,
}: {
  runId: number;
  jobId: number;
  platform: string;
  period: string;
  inFlight: boolean;
}) {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      {result && (
        <div className={`notice ${result.ok ? "good" : "bad"}`} aria-live="polite">
          {result.message}
        </div>
      )}

      {inFlight ? (
        <>
          <p className="small" style={{ marginTop: 0 }}>
            This run is still going. Cancelling stops it before it writes a finance
            file — nothing partial is kept.
          </p>
          <button
            type="button"
            className="secondary"
            disabled={pending}
            onClick={() => start(async () => setResult(await cancelRun(jobId, runId)))}
          >
            {pending ? "Cancelling…" : "Cancel this run"}
          </button>
        </>
      ) : confirming ? (
        <>
          <p className="small" style={{ marginTop: 0 }}>
            <strong>Run {platform} {period} again?</strong> This produces a second
            finance file for the same settlement window. That is the right thing to
            do after fixing an upload or a rule — and the wrong thing to do to a
            result somebody has already invoiced from.
          </p>
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              start(async () => {
                setResult(await requeueRun(platform, period));
                setConfirming(false);
              })
            }
          >
            {pending ? "Queueing…" : "Yes, run it again"}
          </button>{" "}
          <button type="button" className="secondary" onClick={() => setConfirming(false)}>
            Keep this result
          </button>
        </>
      ) : (
        <>
          <p className="small" style={{ marginTop: 0 }}>
            Fixed an upload or a rule since this ran? Queue the window again.
          </p>
          <button type="button" className="secondary" onClick={() => setConfirming(true)}>
            Run this window again
          </button>
        </>
      )}
    </div>
  );
}
