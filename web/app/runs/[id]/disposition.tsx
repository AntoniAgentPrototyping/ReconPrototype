"use client";

import { useActionState, useState } from "react";

import { clearExceptionDisposition, setExceptionDisposition, type ActionResult } from "../../actions";
import { t, type Lang } from "@/lib/words";
import type { ExceptionRow } from "@/lib/api";

/**
 * The decision control on one exception row (**D1**).
 *
 * A decision ANNOTATES the fingerprint — it follows the row across runs as a
 * badge and never hides it, because the fingerprint hashes identity columns,
 * not amounts, and a marked variance that has quietly grown must still be in
 * front of someone.
 *
 * The reason is mandatory and becomes the record the next reader gets — the
 * same typed-reason pattern as removing a file or unpinning the rules.
 */
export function Disposition({ row, runId, lang }: { row: ExceptionRow; runId: number; lang: Lang }) {
  const [marking, setMarking] = useState<"reviewed" | "expected" | null>(null);
  const [reopening, setReopening] = useState(false);
  const [markState, markAction, markPending] = useActionState<ActionResult | null, FormData>(
    setExceptionDisposition,
    null,
  );
  const [clearState, clearAction, clearPending] = useActionState<ActionResult | null, FormData>(
    clearExceptionDisposition,
    null,
  );
  const failure = [markState, clearState].find((s) => s && !s.ok);

  if (row.disposition && !reopening) {
    return (
      <div>
        <span
          className="badge muted"
          title={`${t(lang, "decidedBy")} ${row.disposition_by ?? "—"}${
            row.decided_at ? ` · ${row.decided_at.slice(0, 10)}` : ""
          }`}
        >
          {t(lang, row.disposition === "expected" ? "dispositionExpected" : "dispositionReviewed")}
        </span>
        {row.disposition_reason && (
          <div className="muted small">{row.disposition_reason}</div>
        )}
        <button type="button" className="secondary small" onClick={() => setReopening(true)}>
          {t(lang, "reopen")}
        </button>
      </div>
    );
  }

  if (reopening) {
    return (
      <form action={clearAction}>
        {failure && (
          <div className="notice bad small" aria-live="polite">
            {failure.message}
          </div>
        )}
        <input type="hidden" name="fingerprint" value={row.fingerprint} />
        <input type="hidden" name="run_id" value={runId} />
        <input
          name="reason"
          required
          minLength={8}
          placeholder={t(lang, "decisionReason")}
          aria-label={t(lang, "decisionReason")}
        />
        <button type="submit" disabled={clearPending}>
          {t(lang, "reopen")}
        </button>{" "}
        <button type="button" className="secondary" onClick={() => setReopening(false)}>
          {t(lang, "cancel")}
        </button>
      </form>
    );
  }

  if (marking) {
    return (
      <form action={markAction} title={t(lang, "decisionHint")}>
        {failure && (
          <div className="notice bad small" aria-live="polite">
            {failure.message}
          </div>
        )}
        <input type="hidden" name="fingerprint" value={row.fingerprint} />
        <input type="hidden" name="run_id" value={runId} />
        <input type="hidden" name="disposition" value={marking} />
        <input
          name="reason"
          required
          minLength={8}
          placeholder={t(lang, "decisionReason")}
          aria-label={t(lang, "decisionReason")}
          autoFocus
        />
        <button type="submit" disabled={markPending}>
          {t(lang, "save")}
        </button>{" "}
        <button type="button" className="secondary" onClick={() => setMarking(null)}>
          {t(lang, "cancel")}
        </button>
      </form>
    );
  }

  return (
    <div title={t(lang, "decisionHint")}>
      <button type="button" className="secondary small" onClick={() => setMarking("reviewed")}>
        {t(lang, "markReviewed")}
      </button>{" "}
      <button type="button" className="secondary small" onClick={() => setMarking("expected")}>
        {t(lang, "markExpected")}
      </button>
    </div>
  );
}
