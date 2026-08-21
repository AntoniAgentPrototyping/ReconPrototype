"use client";

import { useActionState } from "react";

import { queueMonthMaster, type ActionResult } from "./actions";
import { t, type Lang } from "@/lib/words";

/**
 * Queue the month-end summary by hand (**A4**).
 *
 * The normal path is automatic — every finished period queues its month's
 * summary. What that path cannot do is REBUILD one when nothing has run: the
 * team's totals arrive late, or a period's rules are repinned. Re-running a
 * period just to trigger the chain would be a second settlement run of that
 * period, which is the shape this system refuses everywhere else.
 *
 * A 409 from the button means a summary is already waiting, which is not a
 * failure: the queued one reads every period finished by the time it runs.
 */
export function MasterForm({ lang, defaultMonth }: { lang: Lang; defaultMonth: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    queueMonthMaster,
    null,
  );

  return (
    <div className="panel">
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`} aria-live="polite">
          {state.message}
        </div>
      )}
      <form className="row" action={action}>
        <div>
          <label htmlFor="master-month">{t(lang, "month")}</label>
          <input id="master-month" type="month" name="month" defaultValue={defaultMonth} required />
        </div>
        <button type="submit" disabled={pending} style={{ alignSelf: "end" }}>
          {pending ? "…" : t(lang, "buildMonthSummary")}
        </button>
      </form>
      <p className="muted small" style={{ marginBottom: 0 }}>
        {t(lang, "buildMonthSummaryHint")}
      </p>
    </div>
  );
}
