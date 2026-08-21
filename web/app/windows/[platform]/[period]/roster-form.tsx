"use client";

import { useActionState, useState, useTransition } from "react";

import { clearRosterDeclaration, declareRoster, type ActionResult } from "../../../actions";
import type { RosterDeclaration } from "@/lib/api";

/**
 * The declaration that replaced the per-run "partial roster" checkbox.
 *
 * The checkbox and this form relax exactly the same hard stop, so the improvement
 * is not in the mechanism — it is that this is stated **once per window**, needs a
 * **reason**, records **who**, and is rendered on the board where somebody
 * reviewing the numbers will see it. A checkbox on the queue form had none of
 * those properties and was ticked by whoever was in a hurry.
 *
 * The reason field is required by the client, the server action, and a database
 * check constraint. Three layers for one sentence is not over-engineering here:
 * the reason is the entire difference between this control and the one it replaced.
 */
export default function RosterForm({
  platform,
  period,
  declaration,
  missingStores,
  expectedStores,
}: {
  platform: string;
  period: string;
  declaration: RosterDeclaration | null;
  /** Expected stores with no file right now — the pre-checked candidates. */
  missingStores: string[];
  /** The window's whole roster, so a store can be declared before its absence
   *  is visible (an early-month declaration). */
  expectedStores: string[];
}) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    declareRoster,
    null,
  );
  const [clearing, startClear] = useTransition();
  const [cleared, setCleared] = useState<ActionResult | null>(null);
  const [partial, setPartial] = useState(declaration?.roster_declared_partial ?? false);

  // D3: which stores the declaration covers. Pre-checked from what it already
  // says, falling back to what is currently missing — the likeliest claim.
  const declared = declaration?.declared_absent_stores;
  const preChecked = new Set(declared ?? missingStores);
  // The roster may not contain a previously declared name (a repin, a rename);
  // keep it visible rather than silently dropping the claim from the form.
  const options = [...new Set([...expectedStores, ...(declared ?? [])])];
  const missing = new Set(missingStores);
  const primary = options.filter((s) => missing.has(s) || preChecked.has(s));
  const rest = options.filter((s) => !missing.has(s) && !preChecked.has(s));

  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`} aria-live="polite">
          {state.message}
        </div>
      )}
      {cleared && (
        <div className={`notice ${cleared.ok ? "good" : "bad"}`} aria-live="polite">
          {cleared.message}
        </div>
      )}

      {declaration ? (
        <p className="muted small">
          Declared {declaration.roster_declared_partial ? "PARTIAL" : "complete"} by{" "}
          <span className="mono">{declaration.declared_by}</span> on{" "}
          {new Date(declaration.declared_at).toLocaleString()}
          {declaration.reason ? ` — “${declaration.reason}”` : ""}.
          {declaration.roster_declared_partial &&
            (declaration.declared_absent_stores ? (
              <>
                {" "}
                Covers: <span className="mono">{declaration.declared_absent_stores.join(", ")}</span>.
              </>
            ) : (
              <>
                {" "}
                <strong>Every</strong> expected store is optional under this declaration —
                including one nobody meant to excuse. Name the absent stores below to
                narrow it.
              </>
            ))}
        </p>
      ) : (
        <p className="muted small">
          No declaration. An incomplete window will stop the run, which is the intended
          behaviour — the store-count check is what caught a real window arriving with 16 of
          17 stores absent.
        </p>
      )}

      <form action={action}>
        <input type="hidden" name="platform" value={platform} />
        <input type="hidden" name="period" value={period} />

        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <input
            type="checkbox"
            name="partial"
            checked={partial}
            onChange={(e) => setPartial(e.target.checked)}
            style={{ width: 16 }}
          />
          <span>
            This window legitimately covers only part of the roster
            {missingStores.length > 0 && (
              <span className="muted"> ({missingStores.length} store(s) currently absent)</span>
            )}
          </span>
        </label>

        {partial && options.length > 0 && (
          <fieldset style={{ border: "none", padding: 0, margin: "0 0 10px" }}>
            <legend className="small" style={{ padding: 0, marginBottom: 4 }}>
              Which stores are legitimately absent? Any expected store you do NOT name
              still stops the run if it has no file — that is what catches a forgotten
              store. Naming none makes every store optional.
            </legend>
            {primary.map((store) => (
              <label key={store} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  name="stores"
                  value={store}
                  defaultChecked={preChecked.has(store)}
                  style={{ width: 16 }}
                />
                <span className="mono small">{store}</span>
                {missing.has(store) && <span className="muted small">— no file yet</span>}
              </label>
            ))}
            {rest.length > 0 && (
              <details style={{ marginTop: 6 }}>
                <summary className="muted small">
                  Other rostered stores ({rest.length}) — declare one absent ahead of time
                </summary>
                {rest.map((store) => (
                  <label key={store} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <input type="checkbox" name="stores" value={store} style={{ width: 16 }} />
                    <span className="mono small">{store}</span>
                  </label>
                ))}
              </details>
            )}
          </fieldset>
        )}

        {partial && (
          <>
            <label htmlFor="reason">Why</label>
            <input
              id="reason"
              name="reason"
              defaultValue={declaration?.reason ?? ""}
              placeholder="e.g. only Masan and Xmenforboss settled in this sub-window"
              minLength={8}
              required
              style={{ width: "100%", marginBottom: 6 }}
            />
            <p className="muted small">
              This sentence appears on the month board next to the window. Write it for the
              person who reads these totals next month and wonders why they are low.
            </p>
          </>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" disabled={pending}>
            {pending ? "Saving…" : "Save declaration"}
          </button>
          {declaration && (
            <button
              type="button"
              className="secondary"
              disabled={clearing}
              onClick={() =>
                startClear(async () => setCleared(await clearRosterDeclaration(platform, period)))
              }
            >
              Withdraw
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
