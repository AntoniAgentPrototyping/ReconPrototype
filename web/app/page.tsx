import { redirect } from "next/navigation";

import { QueueForm } from "./queue-form";
import { api, whoami, type BoardRow } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The month board: one row per settlement window, showing what happened.
 *
 * The column that matters most is `status`, and it is deliberately NOT the same
 * thing as `job_state`. A run that hard-stops on bad input is a job that
 * executed perfectly and a run that concluded nothing was produced — collapsing
 * the two would make a data problem look like a broken worker
 * (docs/06-DECISIONS.md#d30).
 */
export default async function BoardPage({
  searchParams,
}: {
  searchParams: Promise<{ month?: string }>;
}) {
  const me = await whoami();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { month } = await searchParams;
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  const { windows } = await api<{ windows: BoardRow[] }>(`/board${query}`);

  const byPlatform = new Map<string, BoardRow[]>();
  for (const row of windows) {
    byPlatform.set(row.platform, [...(byPlatform.get(row.platform) ?? []), row]);
  }

  return (
    <>
      <h1>Month board</h1>
      <p className="lede">
        {windows.length} window{windows.length === 1 ? "" : "s"}
        {month ? ` in ${month}` : ""}. A window shows its most recent run; the count says how
        many times it has been run.
      </p>

      {me.role !== "recon.viewer" && <QueueForm />}

      {windows.length === 0 && (
        <div className="panel muted">
          Nothing queued yet. {me.role === "recon.viewer" ? "" : "Queue a window above."}
        </div>
      )}

      {[...byPlatform.entries()].map(([platform, rows]) => (
        <section key={platform}>
          <h2>{platform}</h2>
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Run</th>
                  <th>Verdict</th>
                  <th>Findings</th>
                  <th className="num">Wall</th>
                  <th className="num">Peak RSS</th>
                  <th>Rules</th>
                  <th>Requested by</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.platform}/${row.period}`}>
                    <td>
                      <a
                        className="mono"
                        href={`/windows/${row.platform}/${encodeURIComponent(row.period)}`}
                      >
                        {row.period}
                      </a>
                      {row.partial_roster && (
                        <>
                          {" "}
                          {/* The reason is IN the badge's title, not just the fact.
                              A "partial" badge with no explanation is the checkbox
                              this replaced, rendered differently. */}
                          <span
                            className="badge variance"
                            title={
                              row.roster_reason
                                ? `Declared partial by ${row.roster_declared_by}: ${row.roster_reason}`
                                : "A SUBSET of the store roster — these totals are not the month's"
                            }
                          >
                            partial
                          </span>
                        </>
                      )}
                      {!!row.roster_missing && (
                        <span
                          className="muted small"
                          title="Expected stores with no file in this window"
                        >
                          {" "}
                          · {row.roster_missing} store(s) absent
                        </span>
                      )}
                      {row.roster_reason && (
                        <div className="muted small">{row.roster_reason}</div>
                      )}
                      {row.job_count > 1 && (
                        <span className="muted small"> · {row.job_count} runs</span>
                      )}
                    </td>
                    <td>
                      {row.run_id ? (
                        <a href={`/runs/${row.run_id}`}>#{row.run_id}</a>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <Verdict row={row} />
                    </td>
                    <td>
                      {row.finding_count ? (
                        <span>{row.finding_count}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">{row.wall_s ? `${row.wall_s.toFixed(1)}s` : "—"}</td>
                    <td className="num">
                      {row.peak_rss_mb ? `${Math.round(row.peak_rss_mb)} MB` : "—"}
                    </td>
                    <td>
                      {row.config_was_pinned ? (
                        <span className="badge muted" title="Frozen to the config an earlier run of this window used, so an edit since cannot change a re-run">
                          pinned
                        </span>
                      ) : (
                        <span className="muted">disk</span>
                      )}
                    </td>
                    <td className="muted">{row.requested_by ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}

/**
 * The run's own conclusion, falling back to the job's state while it is still
 * queued or in flight. Exit codes are shown because the CLI prints the same
 * numbers, and an operator should not have to learn two vocabularies.
 */
function Verdict({ row }: { row: BoardRow }) {
  if (row.status) {
    const label = row.status.replace("_", " ");
    return (
      <span className={`badge ${row.status}`} title={`exit code ${row.exit_code}`}>
        {label}
      </span>
    );
  }
  if (row.job_state === "leased") return <span className="badge running">running</span>;
  if (row.job_state === "error") return <span className="badge hard_stop">worker error</span>;
  return <span className="badge muted">{row.job_state}</span>;
}
