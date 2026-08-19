"use client";

import { useActionState, useState } from "react";

import { uploadExport, type ActionResult } from "../../../actions";
import type { Lang } from "@/lib/words";

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
  const vi = lang === "vi";
  const totalMb = chosen.reduce((n, f) => n + f.bytes, 0) / (1024 * 1024);

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
            <select id="kind" name="kind" defaultValue={kinds[0]}>
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
              onChange={(e) =>
                setChosen(
                  Array.from(e.target.files ?? []).map((f) => ({
                    name: f.name,
                    bytes: f.size,
                  })),
                )
              }
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
              ? `Đã chọn ${chosen.length} file (${totalMb.toFixed(1)} MB). Tên cửa hàng được đọc từ tên file theo đúng quy tắc hệ thống dùng khi chạy; tên nào không đọc được sẽ bị từ chối kèm lý do.`
              : `${chosen.length} file${chosen.length === 1 ? "" : "s"} selected (${totalMb.toFixed(1)} MB). The store is read from each filename by the same rule the run uses; a name it cannot read is refused with the reason.`}
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
