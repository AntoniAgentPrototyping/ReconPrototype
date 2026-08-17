"use client";

import { useActionState, useState } from "react";

import { queueRun, type ActionResult } from "./actions";

/**
 * Queue a window whose exports have been uploaded.
 *
 * **No "partial roster" checkbox since M6.** It relaxed the store-count hard stop
 * per run, which meant it was ticked by whoever was in a hurry, recorded no reason
 * and was invisible to whoever reviewed the numbers afterwards. The hard stop is
 * unchanged; the override is now a declaration made once per window, with a
 * mandatory reason and a named author, on the window's own page.
 *
 * A 409 here is the double-run guard, not a bug: one settlement window may have
 * only one live job, because two concurrent runs of one window is the
 * double-invoicing shape this pipeline defends against everywhere else
 * (docs/06-DECISIONS.md#d33). The message says so rather than reading as a
 * failure.
 */
export function QueueForm() {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(queueRun, null);
  const [platform, setPlatform] = useState("lazada");
  const [period, setPeriod] = useState("");

  return (
    <div className="panel">
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`}>{state.message}</div>
      )}
      <form className="row" action={action}>
        <div>
          <label htmlFor="platform">Platform</label>
          <select
            id="platform"
            name="platform"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
          >
            <option value="tiktok">tiktok</option>
            <option value="shopee">shopee</option>
            <option value="lazada">lazada</option>
          </select>
        </div>
        <div>
          <label htmlFor="period">Window</label>
          <input
            id="period"
            name="period"
            placeholder="2026-05_l1"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            required
          />
        </div>
        <button type="submit" disabled={pending}>
          {pending ? "Queueing…" : "Queue run"}
        </button>
        {period.trim() && (
          <a
            className="secondary"
            href={`/windows/${platform}/${encodeURIComponent(period.trim())}`}
            style={{ alignSelf: "center" }}
          >
            Open window
          </a>
        )}
      </form>
      <p className="muted small" style={{ marginBottom: 0 }}>
        Upload the exports on the window&apos;s own page first — that screen shows what the
        pipeline will name each file and which expected stores are still missing.
      </p>
    </div>
  );
}
