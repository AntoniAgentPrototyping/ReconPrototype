"use client";

import { useState, useTransition } from "react";

import { rejectUpload, type ActionResult } from "../../../actions";
import type { PlannedFile } from "@/lib/api";

function size(bytes: number | undefined): string {
  if (!bytes) return "—";
  return bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

/**
 * One uploaded file, and the name the pipeline will read it under.
 *
 * A file a run has already read cannot be removed — the API refuses with a 409 —
 * so the button is hidden for a consumed row. That is the same reasoning as
 * `windows`: the window a workbook was produced from must stay reconstructible.
 */
export default function FileRow({
  file,
  platform,
  period,
  canEdit,
}: {
  file: PlannedFile;
  platform: string;
  period: string;
  canEdit: boolean;
}) {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");

  const consumed = file.state === "consumed";

  return (
    <tr>
      <td>
        <span className="mono">{file.filename}</span>
        {consumed && (
          <div className="muted small">read by a run — kept for reconstruction</div>
        )}
        {result && (
          <div className={`notice ${result.ok ? "good" : "bad"}`} style={{ marginTop: 6 }}>
            {result.message}
          </div>
        )}
      </td>
      <td>
        {file.uniform_name ? (
          <span className="mono">{file.uniform_name}</span>
        ) : (
          <span className="badge variance" title="No uniform name could be built — a run would stop on this file">
            unnameable
          </span>
        )}
        {file.renamed === false && <div className="muted small">already uniform</div>}
      </td>
      <td>
        {file.store ? <span className="mono">{file.store}</span> : <span className="muted">—</span>}
      </td>
      <td className="num">{size(file.bytes)}</td>
      <td className="muted small">{file.uploaded_by ?? "—"}</td>
      <td>
        {canEdit && !consumed && !asking && (
          <button type="button" className="secondary" onClick={() => setAsking(true)}>
            Remove
          </button>
        )}
        {canEdit && !consumed && asking && (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="why (at least 8 characters)"
              aria-label={`Reason for removing ${file.filename}`}
              style={{ width: 200 }}
            />
            <button
              type="button"
              disabled={pending || reason.trim().length < 8}
              onClick={() =>
                start(async () => {
                  setResult(
                    await rejectUpload(file.upload_id, platform, period, reason.trim()),
                  );
                  setAsking(false);
                })
              }
            >
              Confirm
            </button>
            <button type="button" className="secondary" onClick={() => setAsking(false)}>
              Cancel
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}
