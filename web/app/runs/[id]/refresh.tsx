"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Keep the run page current while the run is in flight, and stop when it settles
 * (**B3**).
 *
 * **The failure this fixes.** The log streamed live but nothing else on the page
 * did. A run's status, exit code, findings, metrics and artifact list were
 * rendered once, server-side, at the moment the page loaded — so somebody who
 * opened a run while it was queued watched the log fill up, reach its last line,
 * and stop, next to a badge that still said "running" and an empty artifact
 * table. The obvious reading is that the system hung. The actual state was that
 * the page needed a manual reload.
 *
 * **Why `router.refresh()` and not another fetch.** The page is `force-dynamic`
 * and already fetches everything it needs server-side. `refresh()` re-runs that
 * render and reconciles it into the existing tree — no second data path to keep
 * in step with the first, and no client-side copy of the payload shape.
 *
 * **Stopping matters as much as starting.** A settled run is immutable: status,
 * findings and artifacts cannot change again. Polling on would be pure load on
 * every open tab, and at month end there are a lot of open tabs. The poll ends on
 * the first render where `inFlight` is false, and `finished` latches so a
 * re-render cannot restart it.
 *
 * The interval is deliberately slower than the log's 1.5s: the log is what
 * somebody is actually reading, and these fields change once or twice in a run.
 */
const INTERVAL_MS = 4000;

export function RunRefresh({ inFlight }: { inFlight: boolean }) {
  const router = useRouter();
  const [finished, setFinished] = useState(!inFlight);

  useEffect(() => {
    if (!inFlight) {
      setFinished(true);
      return;
    }
    if (finished) return;

    const timer = setInterval(() => router.refresh(), INTERVAL_MS);
    return () => clearInterval(timer);
  }, [inFlight, finished, router]);

  if (!inFlight) return null;
  return (
    <span className="muted small" aria-live="polite">
      · updating automatically
    </span>
  );
}
