"use client";

import { useState, useTransition } from "react";

import { proposeEdits } from "../../../actions";
import type { KindDrift } from "@/lib/api";
import { t, type Lang } from "@/lib/words";

/**
 * Format drift, for the person who has to absorb it (register D5).
 *
 * `docs/12-CHANGE-HISTORY.md` logs 18 drift events in three months and every one
 * needed a developer: the platform renamed a column, the run hard-stopped ~200
 * seconds in with *"income data is missing required columns after header mapping:
 * ['net_revenue']. Update column_maps.income in settings.yaml"*, and somebody who
 * knew what a column map was went and edited YAML. Nothing in the product helped.
 *
 * This is the two halves of the help, and neither is a new control:
 *
 * 1. **The evidence.** Which headers these files carry that the rules do not name,
 *    per file, PII excluded — because an untriaged list containing `Recipient` and
 *    `Phone #` is a list an operator learns to ignore. A renamed column looks
 *    exactly like this.
 * 2. **The one step from evidence to fix.** Map an unknown header to a field the
 *    pipeline understands, as a config proposal. It changes nothing by itself:
 *    approve/apply, `invalidates_goldens` and the verification canary all still
 *    stand between this form and a settlement run.
 *
 * **No suggestion, and that is deliberate.** The screen shows the unknown headers
 * and the fields nothing supplies; the human pairs them. Ranking candidates by name
 * similarity is precisely what [D7](docs/06-DECISIONS.md#d7) exists to refuse —
 * `Pediasure` → `Abbott Pediasure` was accepted on order-ID-overlap evidence, and a
 * storefront that *looked* like an alias was proven genuinely new at zero overlap.
 * A suggested mapping is a future pass with its own plan
 * (`10-ROADMAP.md#where-ai-does-and-does-not-belong`), and it needs to read cell
 * values, which are PII-bearing. Not decoration on this one.
 */
export default function DriftPanel({
  platform,
  period,
  kind,
  drift,
  canonicalFields,
  canEdit,
  lang,
}: {
  platform: string;
  period: string;
  kind: string;
  drift: KindDrift;
  canonicalFields: string[];
  canEdit: boolean;
  lang: Lang;
}) {
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, start] = useTransition();
  const vi = lang === "vi";

  const files = Object.entries(drift.unrecognised_headers);
  // Nothing to say and nothing measured are different sentences. `checked: false`
  // means Lazada (no required field set) or files that predate the header record.
  if (drift.missing_fields.length === 0 && files.length === 0) {
    return (
      <p className="muted small">
        {drift.checked ? t(lang, "driftNothing") : t(lang, "driftUnchecked")}
      </p>
    );
  }

  function propose(header: string, canonical: string, filename: string) {
    if (!canonical) return;
    setNotice(null);
    start(async () => {
      const result = await proposeEdits(
        [
          {
            table: "config_column_maps",
            op: "upsert",
            key: { platform, kind, raw_header: header },
            values: { canonical, active: true },
            // The provenance, not a justification anyone has to invent: this
            // header was observed in this file, in this window.
            evidence: `Seen as an unknown column in ${filename} (${platform} ${period}).`,
          },
        ],
        `Map the ${platform} ${kind} column "${header}" to ${canonical}`,
      );
      setNotice(result);
    });
  }

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h3 style={{ marginTop: 0 }}>{t(lang, "driftHeading")}</h3>

      {notice && (
        <div className={`notice ${notice.ok ? "good" : "bad"}`} role="alert">
          {notice.message}
        </div>
      )}

      {drift.missing_fields.length > 0 && (
        <div className="notice bad" role="alert">
          <strong>{t(lang, "driftMissing")}</strong>
          <div>{drift.missing_fields.join(", ")}</div>
          <div className="small">{t(lang, "driftMissingHint")}</div>
        </div>
      )}

      {files.length === 0 ? null : (
        <>
          <p className="muted small">{t(lang, "driftIntro")}</p>
          <table>
            <thead>
              <tr>
                <th>{vi ? "File" : "File"}</th>
                <th>{vi ? "Cột" : "Column"}</th>
                <th>{t(lang, "driftMapTo")}</th>
              </tr>
            </thead>
            <tbody>
              {files.flatMap(([filename, headers]) =>
                headers.map((header) => (
                  <tr key={`${filename}:${header}`}>
                    <td style={{ wordBreak: "break-all" }}>{filename}</td>
                    <td>
                      <code>{header}</code>
                    </td>
                    <td>
                      {canEdit ? (
                        <HeaderMapper
                          header={header}
                          fields={canonicalFields}
                          pending={pending}
                          lang={lang}
                          onPropose={(canonical) => propose(header, canonical, filename)}
                        />
                      ) : (
                        <span className="muted small">—</span>
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function HeaderMapper({
  header,
  fields,
  pending,
  lang,
  onPropose,
}: {
  header: string;
  fields: string[];
  pending: boolean;
  lang: Lang;
  onPropose: (canonical: string) => void;
}) {
  const [chosen, setChosen] = useState("");
  return (
    <div className="row" style={{ gap: 8 }}>
      <select
        value={chosen}
        onChange={(e) => setChosen(e.target.value)}
        aria-label={`${t(lang, "driftMapTo")} — ${header}`}
      >
        <option value="">{t(lang, "driftPick")}</option>
        {fields.map((field) => (
          <option key={field} value={field}>
            {field}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={!chosen || pending}
        onClick={() => onPropose(chosen)}
      >
        {t(lang, "driftPropose")}
      </button>
    </div>
  );
}
