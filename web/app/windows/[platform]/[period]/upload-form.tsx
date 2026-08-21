"use client";

import { useActionState, useState, useTransition } from "react";

import { previewStores, uploadExport, type ActionResult } from "../../../actions";
import type { StorePreview } from "@/lib/api";
import { t, type Lang } from "@/lib/words";

/**
 * Pick a kind, pick files, check the store each one will become, upload.
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
 *
 * **The store row is register D7, and it closes a two-sided gap.** `POST /uploads`
 * has accepted a `store` field since M6 — to confirm or correct what the filename
 * pattern found — and the upload action has posted `store:<filename>` for just as
 * long. Nothing ever rendered an input, so the documented affordance was
 * unreachable: an operator whose file parsed to the wrong storefront could only
 * rename it on disk and try again. Now each chosen file shows what the pipeline's
 * own rule reads out of its name, whether that storefront is on this window's
 * roster, and a correction that is a picklist rather than free text.
 *
 * **The derivation is never done here.** `previewStores` asks the API, which calls
 * the same `naming.store_of` the upload and the run use. A regex in the browser
 * would be a second definition of store identity (docs/06-DECISIONS.md#d6) — the
 * single most invasive drift this system could acquire, since it decides whose
 * revenue a file becomes.
 *
 * **On progress (B10, partially done and honestly labelled).** There is no byte-level
 * progress bar. This posts through a server action, and a server action gives the
 * browser no upload-progress events — getting a real bar means rewriting this as an
 * XHR with `upload.onprogress`, which would also mean re-implementing the per-file
 * refusal handling above, and that handling is the part that matters at month end.
 * What is here instead is the honest half: the count and the total size, so a person
 * watching a 382 MB window upload knows the wait is expected rather than a hang.
 */
export default function UploadForm({
  platform,
  period,
  kinds,
  lang,
}: {
  platform: string;
  period: string;
  kinds: string[];
  lang: Lang;
}) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(
    uploadExport,
    null,
  );
  const [chosen, setChosen] = useState<{ name: string; bytes: number }[]>([]);
  const [kind, setKind] = useState(kinds[0]);
  const [preview, setPreview] = useState<StorePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [checking, startChecking] = useTransition();
  const vi = lang === "vi";
  const totalMb = chosen.reduce((n, f) => n + f.bytes, 0) / (1024 * 1024);

  /**
   * Ask what each name resolves to. Called on both inputs, because the answer
   * depends on the kind as well as the name — the uniform name a file will carry
   * contains it, and TikTok's pattern reads `order`/`income` out of the name.
   */
  function check(names: string[], forKind: string) {
    setPreview(null);
    setPreviewError("");
    if (names.length === 0) return;
    startChecking(async () => {
      const result = await previewStores(platform, period, forKind, names);
      if (result.ok) setPreview(result.preview);
      else setPreviewError(result.message);
    });
  }

  return (
    <div className="panel">
      {state && (
        <div className={`notice ${state.ok ? "good" : "bad"}`} aria-live="polite">
          {state.message}
        </div>
      )}

      <form action={action}>
        <input type="hidden" name="platform" value={platform} />
        <input type="hidden" name="period" value={period} />

        <div className="row">
          <div>
            <label htmlFor="kind">{vi ? "Loại file" : "File kind"}</label>
            <select
              id="kind"
              name="kind"
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                check(chosen.map((f) => f.name), e.target.value);
              }}
            >
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label htmlFor="file">{vi ? "File xuất từ sàn" : "Exports"}</label>
            <input
              id="file"
              name="file"
              type="file"
              multiple
              accept=".xlsx,.xls,.csv"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []).map((f) => ({
                  name: f.name,
                  bytes: f.size,
                }));
                setChosen(picked);
                check(picked.map((f) => f.name), kind);
              }}
              style={{ width: "100%" }}
            />
          </div>
          <button type="submit" disabled={pending}>
            {pending
              ? vi
                ? "Đang tải lên…"
                : "Uploading…"
              : `${vi ? "Tải lên" : "Upload"}${chosen.length ? ` ${chosen.length}` : ""}`}
          </button>
        </div>

        {/* D7: one row per chosen file, with the store it will become. Inside the
            form, because the corrections ARE form fields — `store:<filename>`,
            which is the name `uploadExport` has always read. */}
        {chosen.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {checking && (
              <div className="muted small" aria-live="polite">
                {t(lang, "storeChecking")}
              </div>
            )}
            {previewError && (
              <div className="notice bad" role="alert">
                {previewError}
              </div>
            )}
            {preview && (
              <>
                <p className="muted small" style={{ marginTop: 0 }}>
                  {t(lang, "storeCorrectionHint")}
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>{vi ? "File" : "File"}</th>
                      <th>{t(lang, "storeFromName")}</th>
                      <th>{t(lang, "storeCorrect")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.files.map((file) => (
                      <tr key={file.filename}>
                        <td style={{ wordBreak: "break-all" }}>{file.filename}</td>
                        <td>
                          {file.problem ? (
                            <>
                              <strong>{t(lang, "storeUnreadable")}</strong>
                              <div className="muted small">{file.problem}</div>
                            </>
                          ) : (
                            <>
                              {file.canonical}
                              <div className="muted small">
                                {file.on_roster === true
                                  ? t(lang, "storeOnRoster")
                                  : file.on_roster === false
                                    ? t(lang, "storeNotOnRoster")
                                    : t(lang, "storeNotChecked")}
                              </div>
                            </>
                          )}
                        </td>
                        <td>
                          {/* A picklist where a roster exists: a store the roster
                              does not name is refused at the door anyway, and the
                              fix for that is a config proposal, not a free-text
                              box that produces the same refusal more slowly. Free
                              text where there is NO roster (Lazada, register A6),
                              because there is nothing to pick from and the door
                              accepts-and-reports there. */}
                          {preview.roster_checked ? (
                            <select
                              name={`store:${file.filename}`}
                              defaultValue=""
                              aria-label={`${t(lang, "storeCorrect")} — ${file.filename}`}
                            >
                              <option value="">
                                {t(lang, "storeKeepDerived")}
                              </option>
                              {preview.expected_stores.map((store) => (
                                <option key={store} value={store}>
                                  {store}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              name={`store:${file.filename}`}
                              placeholder={t(lang, "storeKeepDerived")}
                              aria-label={`${t(lang, "storeCorrect")} — ${file.filename}`}
                            />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}

        {/* B10: a busy state that says how much work is outstanding. Without it a
            large window looks like a hung page for minutes at a time. */}
        {pending && chosen.length > 0 && (
          <div className="small" style={{ marginTop: 8 }} aria-live="polite">
            {vi
              ? `Đang tải ${chosen.length} file (${totalMb.toFixed(1)} MB). File lớn có thể mất vài phút — đừng đóng trang này.`
              : `Uploading ${chosen.length} file${chosen.length === 1 ? "" : "s"} (${totalMb.toFixed(1)} MB). Large files can take a few minutes — do not close this page.`}
          </div>
        )}

        {!pending && chosen.length > 0 && (
          <div className="muted small" style={{ marginTop: 8 }}>
            {vi
              ? `Đã chọn ${chosen.length} file (${totalMb.toFixed(1)} MB).`
              : `${chosen.length} file${chosen.length === 1 ? "" : "s"} selected (${totalMb.toFixed(1)} MB).`}
          </div>
        )}
      </form>

      <p className="muted small" style={{ marginBottom: 0, marginTop: 10 }}>
        {vi
          ? "Khi chạy, file sẽ được đổi sang tên theo quy chuẩn. Nếu tải lên đúng file cũ (trùng từng byte) thì hệ thống từ chối — đó chính là kiểu lỗi tải trùng, và một lần như vậy từng suýt gây xuất hoá đơn trùng 5,97 tỷ VND."
          : "Files are renamed to a uniform scheme when a run reads them, and the same bytes uploaded twice are refused — that duplicate is the double-pull shape, and one instance of it carried 5.97B VND of double-invoicing risk."}
      </p>
    </div>
  );
}
