"use client";

import { useState, useTransition } from "react";

import { reclaimJobs, type ActionResult } from "./actions";

/**
 * The way out of a stuck queue, for an admin who has no shell (**C1**).
 *
 * **The situation this exists for.** A worker dies mid-run. Its job stays
 * `leased`, its run stays in flight, and the board shows the window as running
 * forever. The sweep that fixes this already ran at the top of every worker loop
 * turn — but if the dead worker was the only worker, nothing is left to sweep,
 * and the fix required a database URL and someone who knew the schema.
 *
 * **Not shown unless it is relevant.** The button appears only when the board has
 * a window that looks like it is running, so it is not a permanent invitation to
 * press something that ends other people's work.
 *
 * **It does not retry.** With `max_attempts` at its default of 1, reclaiming
 * marks the job failed and closes its run; it does not run the window again. That
 * is the point — an automatic retry of a settlement run is a second write of the
 * same money, and the person reading the result should decide.
 */
export function ReclaimButton() {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [asking, setAsking] = useState(false);

  return (
    <div style={{ marginBottom: 12 }}>
      {result && (
        <div className={`notice ${result.ok ? "good" : "bad"}`} aria-live="polite">
          {result.message}
        </div>
      )}
      {asking ? (
        <div className="notice">
          <span className="small">
            A window has been showing as running for a while? This closes out jobs
            whose worker stopped responding. If a worker is in fact alive and just
            slow, this ends a run that was going to finish — so check first that
            nothing is progressing.
          </span>{" "}
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              start(async () => {
                setResult(await reclaimJobs());
                setAsking(false);
              })
            }
          >
            {pending ? "Checking…" : "Yes, close them out"}
          </button>{" "}
          <button type="button" className="secondary" onClick={() => setAsking(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <button type="button" className="secondary" onClick={() => setAsking(true)}>
          A run appears stuck
        </button>
      )}
    </div>
  );
}
