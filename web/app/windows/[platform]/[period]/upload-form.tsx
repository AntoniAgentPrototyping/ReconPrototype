"use client";

import { useActionState, useState } from "react";

import { uploadExport, type ActionResult } from "../../../actions";

/**
 * Pick a kind, pick files, upload.
 *
 * `multiple` because a real window is 3-39 files and one-at-a-time is how an
 * operator ends up back in a folder copying things by hand. Each file is posted
 * separately by the action, and one refusal does not abandon the rest — at month
 * end, losing eleven uploads because the fourth was the wrong kind is the
 * difference between a tool and a chore.
 *
 * The kind is asked for rather than guessed. Shopee's own filenames say
 * `Order`/`Income` and TikTok's say `order`/`income`, so guessing would work most
 * of the time — and a wrong guess strips the file against the wrong column map,
 * which surfaces as "none of the configured columns are present" and sends the
 * operator to look at the export rather than at the dropdown.
 */
export default function UploadForm({
  platform,
  period,
  kinds,
}: {
  platform: string;
  period: string;
  kinds: string[];
}) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    uploadExport,
    null,
  );
  const [chosen, setChosen] = useState<string[]>([]);

  return (
    <div className="panel">
      {state && <div className={`notice ${state.ok ? "good" : "bad"}`}>{state.message}</div>}

      <form action={action}>
        <input type="hidden" name="platform" value={platform} />
        <input type="hidden" name="period" value={period} />

        <div className="row">
          <div>
            <label htmlFor="kind">File kind</label>
            <select id="kind" name="kind" defaultValue={kinds[0]}>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label htmlFor="file">Exports</label>
            <input
              id="file"
              name="file"
              type="file"
              multiple
              accept=".xlsx,.xls,.csv"
              onChange={(e) =>
                setChosen(Array.from(e.target.files ?? []).map((f) => f.name))
              }
              style={{ width: "100%" }}
            />
          </div>
          <button type="submit" disabled={pending}>
            {pending ? "Uploading…" : `Upload${chosen.length ? ` ${chosen.length}` : ""}`}
          </button>
        </div>

        {chosen.length > 0 && (
          <div className="muted small" style={{ marginTop: 8 }}>
            {chosen.length} file{chosen.length === 1 ? "" : "s"} selected. The store is read
            from each filename by the same rule the pipeline uses; a name it cannot parse is
            refused with the reason.
          </div>
        )}
      </form>

      <p className="muted small" style={{ marginBottom: 0, marginTop: 10 }}>
        Files are renamed to a uniform scheme when a run reads them, and the same bytes
        uploaded twice are refused — that duplicate is the double-pull shape, and one
        instance of it carried 5.97B VND of double-invoicing risk.
      </p>
    </div>
  );
}
