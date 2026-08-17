import { redirect } from "next/navigation";

import { ExceptionQueue } from "./exceptions";
import { RunLog } from "./log";
import { api, whoami, type ExceptionSheet, type Run } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * One run: what it concluded, what it logged, what it flagged, what it produced.
 *
 * `findings` is rendered as two lists rather than one, exactly as `run_log.txt`
 * prints it. When variances and unchecked stores shared a single channel, a run
 * that was simply never compared printed one alarming line per store — which is
 * how an operator learns to ignore the list (docs/08-KNOWN-DEFECTS.md#11).
 */
export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const me = await whoami();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { id } = await params;
  const runId = Number(id);
  const run = await api<Run>(`/runs/${runId}`);
  const sheets: ExceptionSheet[] = run.exception_sheets ?? [];

  return (
    <>
      <h1>
        <span className="mono">
          {run.platform} {run.period}
        </span>{" "}
        · run #{run.id}
      </h1>
      <p className="lede">
        {run.in_flight ? (
          <span className="badge running">running</span>
        ) : (
          <span className={`badge ${run.status ?? "muted"}`}>
            {(run.status ?? "unknown").replace("_", " ")} · exit {run.exit_code}
          </span>
        )}{" "}
        {run.config_was_pinned && (
          <span className="badge muted" title="Frozen to the config an earlier run of this window used">
            rules pinned · version {run.config_version_id}
          </span>
        )}
      </p>

      {run.error && <div className="notice bad mono small">{run.error}</div>}

      {run.status === "unverified" && (
        <div className="notice">
          This run had nothing to check against — no team reference totals were supplied. That is
          a gap in checking, not a disagreement.
        </div>
      )}

      <div className="panel">
        <table>
          <tbody>
            <Metric label="Wall" value={run.wall_s} suffix="s" />
            <Metric label="I/O" value={run.io_s} suffix="s" />
            <Metric label="Compute" value={run.compute_s} suffix="s" hint="DataFrame math only — the only part a different engine would change" />
            <Metric label="Serialize" value={run.serialize_s} suffix="s" hint="openpyxl workbook building — engine-independent" />
            <Metric label="Peak RSS" value={run.peak_rss_mb} suffix=" MB" round />
          </tbody>
        </table>
      </div>

      {(run.variances.length > 0 || run.unverified.length > 0) && (
        <>
          <h2>Findings</h2>
          <div className="panel">
            {run.variances.length > 0 && (
              <>
                <p className="small" style={{ marginTop: 0 }}>
                  <strong>{run.variances.length} variance(s)</strong> — real numeric
                  disagreements.
                </p>
                <ul className="mono small">
                  {run.variances.map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              </>
            )}
            {run.unverified.length > 0 && (
              <>
                <p className="small">
                  <strong>{run.unverified.length} store(s) not checked</strong> — no team
                  reference found. Not a failure.
                </p>
                <ul className="mono small muted">
                  {run.unverified.map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </>
      )}

      <h2>Artifacts</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th className="num">Size</th>
              <th>SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {run.artifacts.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  Nothing was produced.
                </td>
              </tr>
            )}
            {run.artifacts.map((a) => (
              <tr key={a.name}>
                <td>
                  <a href={`/runs/${run.id}/download/${encodeURIComponent(a.name)}`}>{a.name}</a>
                </td>
                <td className="num">{(a.bytes / 1024).toFixed(1)} KB</td>
                <td
                  className="mono muted small"
                  title="Transfer integrity only — never a content-equality check, because openpyxl stamps a timestamp into every file"
                >
                  {a.bytes_sha256.slice(0, 16)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Exceptions</h2>
      <ExceptionQueue runId={run.id} sheets={sheets} />

      <h2>Run log</h2>
      <RunLog runId={run.id} complete={!run.in_flight} />
    </>
  );
}

function Metric({
  label,
  value,
  suffix,
  round,
  hint,
}: {
  label: string;
  value: number | null;
  suffix: string;
  round?: boolean;
  hint?: string;
}) {
  return (
    <tr>
      <th style={{ width: 140 }} title={hint}>
        {label}
      </th>
      <td className="mono">
        {value === null ? "—" : `${round ? Math.round(value) : value.toFixed(2)}${suffix}`}
      </td>
    </tr>
  );
}
