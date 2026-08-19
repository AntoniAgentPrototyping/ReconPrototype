"use client";

import { useActionState, useState, useTransition } from "react";

import { clearReferences, setReferences, type ActionResult } from "../../../actions";
import type { ReferenceField, WindowReferences } from "@/lib/api";
import type { Lang } from "@/lib/words";

/**
 * The team's own totals for this window — the thing a run gets checked against.
 *
 * **Why this screen did not exist until now.** `src/pipeline.py` has always
 * compared its numbers against reference figures and reported `UNVERIFIED` when
 * there are none. The API has accepted them on a job since M4. But no screen ever
 * sent any, so every run made through a browser has completed with nothing
 * corroborating it — a state the run page reported honestly and which nobody could
 * do anything about from inside the product.
 *
 * **Named fields, not a JSON box.** The field list comes from the API
 * (`service/references.py`), so it cannot drift from the keys the tie-out actually
 * reads. A figure recorded under a name no check compares would look verified and
 * be silently ignored, which is worse than no figure at all.
 *
 * **Blank is not zero.** An empty box means the team did not give us that number
 * and the check skips it. Sending 0 would compare the window against 0 VND and
 * report all of it as a variance. The two are never conflated — the form drops
 * empty fields rather than defaulting them.
 */
export default function ReferencesForm({
  platform,
  period,
  fields,
  references,
  summary,
  lang,
}: {
  platform: string;
  period: string;
  fields: ReferenceField[];
  references: WindowReferences | null;
  summary: string;
  lang: Lang;
}) {
  const vi = lang === "vi";
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    setReferences,
    null,
  );
  const [clearing, startClear] = useTransition();
  const [cleared, setCleared] = useState<ActionResult | null>(null);
  const supplied = references?.refs?.grand ?? {};

  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      <h2 style={{ marginTop: 0 }}>{vi ? "Số của team" : "The team's figures"}</h2>
      <p className="small">
        {vi ? (
          <>
            Nhập tổng số từ file của team. Khi chạy, hệ thống sẽ so số của nó với các
            số này; nếu không có, kết quả sẽ là <strong>chưa đối chiếu</strong> —
            nghĩa là chạy xong không lỗi nhưng chưa có gì độc lập xác nhận. Đơn vị là
            VND, gõ dấu phẩy hay dấu chấm đều được.
          </>
        ) : (
          <>
            Enter the totals from the team&apos;s own file. A run compares its numbers
            against these; without them the result is <strong>not checked</strong> —
            it ran cleanly, but nothing independent confirmed it. Money is VND, and
            separators are fine.
          </>
        )}
      </p>
      <p className="small muted">{summary}</p>

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

      <form action={action}>
        <input type="hidden" name="platform" value={platform} />
        <input type="hidden" name="period" value={period} />

        {fields.map((field) => (
          <div key={field.key} style={{ marginBottom: 12 }}>
            <label htmlFor={`ref-${field.key}`}>
              <strong>{vi ? field.label_vi : field.label}</strong>
            </label>
            <div className="muted small" style={{ margin: "2px 0 4px" }}>
              {vi ? field.help_vi : field.help}
            </div>
            <input
              id={`ref-${field.key}`}
              name={`ref.${field.key}`}
              className="mono"
              inputMode="numeric"
              defaultValue={
                supplied[field.key] === undefined ? "" : String(supplied[field.key])
              }
              placeholder={
                vi ? "để trống nếu team không đưa số này" : "leave blank if the team did not give us this"
              }
              style={{ width: "100%" }}
            />
          </div>
        ))}

        <div style={{ marginBottom: 12 }}>
          <label htmlFor="ref-note">
            <strong>{vi ? "Số này lấy từ đâu" : "Where these came from"}</strong>
          </label>
          <div className="muted small" style={{ margin: "2px 0 4px" }}>
            {vi
              ? "Không bắt buộc, nhưng nên ghi một câu — file nào, sheet nào, ngày nào. Nếu sau này số của team và số hệ thống lệch nhau, đây là thứ cho biết nên nghi ngờ bên nào."
              : "Optional, and worth a sentence — which file, which tab, which date. If these figures and ours disagree later, this is what tells whoever looks into it which of the two to doubt."}
          </div>
          <input
            id="ref-note"
            name="note"
            defaultValue={references?.note ?? ""}
            style={{ width: "100%" }}
          />
        </div>

        <button type="submit" disabled={pending}>
          {pending ? (vi ? "Đang lưu…" : "Saving…") : vi ? "Lưu số" : "Save figures"}
        </button>{" "}
        {references && (
          <button
            type="button"
            className="secondary"
            disabled={clearing}
            onClick={() =>
              startClear(async () => setCleared(await clearReferences(platform, period)))
            }
          >
            {clearing ? (vi ? "Đang rút…" : "Withdrawing…") : vi ? "Rút lại" : "Withdraw"}
          </button>
        )}
      </form>

      {references && (
        <p className="muted small" style={{ marginTop: 10 }}>
          {vi ? "Do" : "Supplied by"} {references.supplied_by}{" "}
          {vi ? "nhập ngày" : "on"}{" "}
          {new Date(references.supplied_at).toLocaleDateString(vi ? "vi-VN" : "en-GB")}.
        </p>
      )}
    </div>
  );
}
