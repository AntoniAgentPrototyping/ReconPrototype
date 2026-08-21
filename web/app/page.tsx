import { redirect } from "next/navigation";

import { MasterForm } from "./master-form";
import { QueueForm } from "./queue-form";
import { ReclaimButton } from "./reclaim-button";
import { api, whoami, type BoardRow } from "@/lib/api";
import { currentLang } from "@/lib/lang";
import { jobState, t, verdict, type Lang } from "@/lib/words";

export const dynamic = "force-dynamic";

// B10: every page shared one browser-tab title, so four windows open at month
// end were four identical tabs. The layout supplies the "· Recon" suffix.
export const metadata = { title: "Các kỳ đối soát" };

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
  const [me, lang] = await Promise.all([whoami(), currentLang()]);
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { month } = await searchParams;
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  // `month_masters` is a separate list, not a window with platform 'all' — a
  // month-end summary rendered as a settlement period would invite someone to
  // read it as one more window (M8 Phase 3).
  const [{ windows, month_masters = [] }, { months }] = await Promise.all([
    api<{ windows: BoardRow[]; month_masters?: BoardRow[] }>(`/board${query}`),
    // D2: which months exist, so the filter is a picker rather than a URL you
    // have to know to edit.
    api<{ months: string[] }>("/months"),
  ]);

  const byPlatform = new Map<string, BoardRow[]>();
  for (const row of windows) {
    byPlatform.set(row.platform, [...(byPlatform.get(row.platform) ?? []), row]);
  }

  return (
    <>
      <h1>{t(lang, "board")}</h1>
      <p className="lede">
        {lang === "vi"
          ? `${windows.length} kỳ${month ? ` trong ${month}` : ""}. Mỗi kỳ hiển thị lần chạy gần nhất; con số bên cạnh cho biết kỳ đó đã chạy bao nhiêu lần.`
          : `${windows.length} settlement period${windows.length === 1 ? "" : "s"}${month ? ` in ${month}` : ""}. Each shows its most recent run; the count beside it says how many times it has been run.`}
      </p>

      {/* D2: the month filter existed only as a URL parameter somebody had to
          know about. A plain GET form, so it works without any client code. */}
      {months.length > 0 && (
        <form className="row" action="/" method="get" style={{ marginBottom: 12 }}>
          <div>
            <label htmlFor="month-filter">{t(lang, "month")}</label>
            <select id="month-filter" name="month" defaultValue={month ?? ""}>
              <option value="">{t(lang, "allMonths")}</option>
              {months.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" className="secondary" style={{ alignSelf: "end" }}>
            {t(lang, "show")}
          </button>
        </form>
      )}

      {me.role !== "recon.viewer" && (
        <QueueForm known={windows.map((w) => ({ platform: w.platform, period: w.period }))} />
      )}
      {/* C1: only when something is actually showing as running — this ends other
          people's in-flight work and should not be a standing invitation. */}
      {me.role === "recon.admin" && windows.some((w) => w.job_state === "leased") && <ReclaimButton />}

      {windows.length === 0 && (
        <div className="panel muted">
          {t(lang, "nothingQueued")}{" "}
          {me.role === "recon.viewer"
            ? ""
            : lang === "vi"
              ? "Chọn một kỳ ở trên để chạy."
              : "Start one from the form above."}
        </div>
      )}

      {/* The month-end summary. Above the windows because it is the thing that
          covers all of them, and separate because it is not a settlement period:
          it consolidates the month's finished windows and is REBUILT whenever
          another one finishes, so it is partial for most of the month. Its own
          run page names which windows it covers and which it does not. */}
      {(month_masters.length > 0 || me.role !== "recon.viewer") && (
        <section>
          <h2>{t(lang, "monthSummary")}</h2>
          {/* A4: the summary is queued automatically when a period finishes; the
              form exists for rebuilding one when nothing has run — a late
              reference total, a repinned period. */}
          {me.role !== "recon.viewer" && (
            <MasterForm lang={lang} defaultMonth={month ?? months[0] ?? ""} />
          )}
          {month_masters.length > 0 && (
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>{lang === "vi" ? "Tháng" : "Month"}</th>
                  <th>{t(lang, "run")}</th>
                  <th>{t(lang, "verdict")}</th>
                  <th>{lang === "vi" ? "Kỳ còn thiếu" : "Periods not included"}</th>
                  <th>{t(lang, "requestedBy")}</th>
                </tr>
              </thead>
              <tbody>
                {month_masters.map((row) => (
                  <tr key={`master/${row.period}`}>
                    <td className="mono">{row.period}</td>
                    <td>
                      {row.run_id ? (
                        <a href={`/runs/${row.run_id}`}>#{row.run_id}</a>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <Verdict row={row} lang={lang} />
                    </td>
                    <td>
                      {row.finding_count ? (
                        <span>{row.finding_count}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="muted">{row.requested_by ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </section>
      )}

      {[...byPlatform.entries()].map(([platform, rows]) => (
        <section key={platform}>
          <h2>{platform}</h2>
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>{t(lang, "window")}</th>
                  <th>{t(lang, "run")}</th>
                  <th>{t(lang, "verdict")}</th>
                  <th>{t(lang, "findings")}</th>
                  <th className="num">{t(lang, "duration")}</th>
                  {/* Was "Peak RSS" — an engine-port trigger measurement, in a
                      table finance reads. */}
                  <th className="num">{t(lang, "memory")}</th>
                  <th>{t(lang, "rules")}</th>
                  <th>{t(lang, "requestedBy")}</th>
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
                                : t(lang, "partialHint")
                            }
                          >
                            {t(lang, "partial")}
                          </span>
                        </>
                      )}
                      {!!row.roster_missing && (
                        <span
                          className="muted small"
                          title={
                            lang === "vi"
                              ? "Cửa hàng dự kiến có mặt nhưng chưa có file nào trong kỳ này"
                              : "Stores expected in this period that have no file"
                          }
                        >
                          {" "}
                          · {row.roster_missing} {t(lang, "storesAbsent")}
                        </span>
                      )}
                      {row.roster_reason && (
                        <div className="muted small">{row.roster_reason}</div>
                      )}
                      {row.job_count > 1 && (
                        <span className="muted small">
                          {" "}
                          · {row.job_count} {lang === "vi" ? "lần chạy" : "runs"}
                        </span>
                      )}
                      {/* D2: a window known only from its uploads — say what
                          there is, which is the files waiting to be run. */}
                      {row.upload_count > 0 && !row.run_id && (
                        <span className="muted small">
                          {" "}
                          · {row.upload_count} {t(lang, "filesUploaded")}
                        </span>
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
                      <Verdict row={row} lang={lang} />
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
                        <span className="badge muted" title={t(lang, "rulesFrozenHint")}>
                          {t(lang, "rulesFrozen")}
                        </span>
                      ) : (
                        <span className="muted">{t(lang, "rulesCurrent")}</span>
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
 * The run's own conclusion, falling back to the queue state while it is still
 * waiting or running.
 *
 * **Was `ok` / `variance` / `unverified` / `hard stop`, with `exit code 3` in the
 * tooltip** (B6) — the command line's vocabulary, and an exit code is a number only
 * `echo $?` cares about. The word now says what happened and the tooltip says what
 * to do about it.
 *
 * In Vietnamese these are the team's OWN phrases, lifted from
 * `src/finance_template.py`'s `VERDICT_OK` / `VERDICT_BAD`: `ok có thể xuất HD` and
 * `Cần check lại số có vấn đề` are what they already write in their workbooks. A
 * more "correct" translation of *variance* would make the screen read like a
 * different system from the file it produces.
 */
function Verdict({ row, lang }: { row: BoardRow; lang: Lang }) {
  if (row.status) {
    const v = verdict(lang, row.status);
    return (
      <span className={`badge ${row.status}`} title={v.hint[lang]}>
        {v[lang]}
      </span>
    );
  }
  // D2: a window with uploads (or a declaration) and no job at all.
  if (!row.job_state) return <span className="badge muted">{t(lang, "notRun")}</span>;
  if (row.job_state === "leased")
    return <span className="badge running">{jobState(lang, "leased")}</span>;
  if (row.job_state === "error")
    return <span className="badge hard_stop">{jobState(lang, "error")}</span>;
  return <span className="badge muted">{jobState(lang, row.job_state)}</span>;
}
