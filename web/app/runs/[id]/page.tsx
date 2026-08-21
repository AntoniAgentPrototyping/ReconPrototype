import { notFound, redirect } from "next/navigation";

import { ExceptionQueue } from "./exceptions";
import { RunLog } from "./log";
import { RunRefresh } from "./refresh";
import { RunActions } from "./run-actions";
import { api, ApiError, whoami, type ExceptionSheet, type Run } from "@/lib/api";
import { currentLang } from "@/lib/lang";
import { t, verdict, type Lang } from "@/lib/words";

export const dynamic = "force-dynamic";

export const metadata = { title: "Lần chạy" };

/** Produced by every run, useful to engineering, meaningless to finance. */
const DIAGNOSTIC_ARTIFACTS = new Set(["run_metrics.json"]);

/**
 * One run: what it concluded, what it logged, what it flagged, what it produced.
 *
 * `findings` is rendered as two lists rather than one, exactly as `run_log.txt`
 * prints it. When variances and unchecked stores shared a single channel, a run
 * that was simply never compared printed one alarming line per store — which is
 * how an operator learns to ignore the list (docs/08-KNOWN-DEFECTS.md#11).
 */
export default async function RunPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ open?: string }>;
}) {
  const [me, lang] = await Promise.all([whoami(), currentLang()]);
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");

  const { open } = await searchParams;
  const { id } = await params;
  const runId = Number(id);
  if (!Number.isInteger(runId) || runId < 1) notFound();

  // B2: a 404 is "that run is not there", which is a different sentence from "the
  // system failed". Anything else still throws to `error.tsx` — swallowing a 500
  // here would present an outage as a missing page.
  let run: Run;
  try {
    run = await api<Run>(`/runs/${runId}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const sheets: ExceptionSheet[] = run.exception_sheets ?? [];
  // B10: the artifact list is read by people looking for the finance file. Split
  // rather than filtered, so nothing becomes unreachable.
  const deliverables = run.artifacts.filter((a) => !DIAGNOSTIC_ARTIFACTS.has(a.name));
  const diagnostics = run.artifacts.filter((a) => DIAGNOSTIC_ARTIFACTS.has(a.name));

  return (
    <>
      <h1>
        {/* B4: the window this run belongs to was unreachable from here, so
            "the upload was wrong" meant navigating by hand. A month-master run
            has NO window — its platform is 'all' and its period is the month —
            so it links back to the board instead of to a window page that would
            answer "not found" about something that is not a window (A4). */}
        {run.platform === "all" ? (
          <>
            <a href={`/?month=${encodeURIComponent(run.period)}`}>
              {t(lang, "monthSummary")} <span className="mono">{run.period}</span>
            </a>
          </>
        ) : (
          <a className="mono" href={`/windows/${run.platform}/${run.period}`}>
            {run.platform} {run.period}
          </a>
        )}{" "}
        · run #{run.id}
      </h1>
      <p className="lede">
        {run.in_flight ? (
          <>
            <span className="badge running">{lang === "vi" ? "đang chạy" : "running"}</span>
            <RunRefresh inFlight />
          </>
        ) : (
          /* B6: was `hard stop · exit 3`. The exit code is a number for `echo $?`
             and told a finance user nothing; the verdict's own sentence follows it
             instead. */
          <span className={`badge ${run.status ?? "muted"}`}>
            {verdict(lang, run.status)[lang]}
          </span>
        )}{" "}
        {run.config_was_pinned && (
          <span className="badge muted" title={t(lang, "rulesFrozenHint")}>
            {t(lang, "rules")}: {t(lang, "rulesFrozen")} · {t(lang, "rulesVersion")}{" "}
            {run.config_version_id}
          </span>
        )}
      </p>

      {run.error && <div className="notice bad mono small">{run.error}</div>}

      {/* A4: what this run queued next — including a FAILURE to queue the month
          summary, which used to be visible only on the worker's own terminal. */}
      {run.chained && (
        <p
          className={run.chained.startsWith("could not") ? "notice bad small" : "muted small"}
          aria-live="polite"
        >
          {run.chained}
        </p>
      )}

      {!run.in_flight && run.status && verdict(lang, run.status).hint[lang] && (
        /* B9: the verdict's meaning, as text rather than only as a tooltip — a
           tooltip is unreachable on a touch screen and invisible to a screen
           reader. */
        <div className="notice" aria-live="polite">
          {verdict(lang, run.status).hint[lang]}
        </div>
      )}

      <div className="panel">
        <table>
          <tbody>
            {/* B6: was Wall / I/O / Compute / Serialize / Peak RSS, hinted with
                "DataFrame math" and "openpyxl workbook building" — two Python
                library names and an acronym for resident set size. The numbers are
                unchanged; only what they are called is. */}
            <Metric label={t(lang, "timeTotal")} value={run.wall_s} suffix="s" />
            <Metric label={t(lang, "timeReading")} value={run.io_s} suffix="s" />
            <Metric label={t(lang, "timeCalculating")} value={run.compute_s} suffix="s" />
            <Metric label={t(lang, "timeWriting")} value={run.serialize_s} suffix="s" />
            <Metric label={t(lang, "memory")} value={run.peak_rss_mb} suffix=" MB" round />
          </tbody>
        </table>
      </div>

      {(run.variances.length > 0 || run.unverified.length > 0) && (
        <>
          <h2>{t(lang, "findings")}</h2>
          <div className="panel">
            {run.variances.length > 0 && (
              <>
                <p className="small" style={{ marginTop: 0 }}>
                  <strong>
                    {run.variances.length}{" "}
                    {lang === "vi" ? "khoản lệch" : "amount(s) that disagree"}
                  </strong>{" "}
                  {lang === "vi"
                    ? "— chênh lệch thật giữa số của hệ thống và số của team."
                    : "— real differences between this system's figures and the team's."}
                </p>
                <ul className="mono small">
                  {run.variances.map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              </>
            )}
            {run.unverified.length > 0 &&
              (nothingWasSupplied(run.unverified) ? (
                /*
                 * A3 / 2.1b: when NOTHING was supplied, one line per store is a
                 * wall of identical scary text for a run that was simply never
                 * checked — which is how an operator learns to ignore the list.
                 * Said once instead.
                 *
                 * Web layer only, deliberately. `RunResult.findings` is one ORDERED
                 * list whose interleaving is committed inside `variances.json`'s
                 * digest, so changing what `_tie` emits would move goldens to
                 * improve a sentence.
                 */
                <p className="small">
                  {lang === "vi" ? (
                    <>
                      <strong>Chưa đối chiếu với gì cả.</strong> Kỳ này chưa có số của
                      team, nên cả {run.unverified.length} cửa hàng đều không được so
                      sánh. Đây là thiếu bước kiểm tra, không phải số sai — số có thể
                      đúng, nhưng chưa có gì xác nhận. Nhập số của team ở trang kỳ.
                    </>
                  ) : (
                    <>
                      <strong>Not checked against anything.</strong> No figures were
                      supplied for this period, so all {run.unverified.length} store
                      {run.unverified.length === 1 ? "" : "s"} went uncompared. That is a
                      gap in checking, not a disagreement — the numbers may well be
                      right, but nothing here says so. Enter the team&apos;s figures on
                      the period page.
                    </>
                  )}
                </p>
              ) : (
                <>
                  <p className="small">
                    <strong>
                      {run.unverified.length}{" "}
                      {lang === "vi" ? "cửa hàng chưa đối chiếu" : "store(s) not checked"}
                    </strong>{" "}
                    {lang === "vi"
                      ? "— không tìm thấy số của team cho những cửa hàng này, dù các cửa hàng khác đã đối chiếu. Không phải lỗi."
                      : "— no figure was supplied for these, though others were checked. Not a failure."}
                  </p>
                  <ul className="mono small muted">
                    {run.unverified.map((v) => (
                      <li key={v}>{v}</li>
                    ))}
                  </ul>
                </>
              ))}
          </div>
        </>
      )}

      {me.role !== "recon.viewer" && (
        <RunActions
          runId={run.id}
          jobId={run.job_id}
          platform={run.platform}
          period={run.period}
          inFlight={run.in_flight}
        />
      )}

      <h2>{t(lang, "filesProduced")}</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>{lang === "vi" ? "File" : "File"}</th>
              <th className="num">{t(lang, "size")}</th>
              <th>{t(lang, "fingerprint")}</th>
            </tr>
          </thead>
          <tbody>
            {deliverables.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  {t(lang, "nothingProduced")}
                </td>
              </tr>
            )}
            {deliverables.map((a) => (
              <tr key={a.name}>
                <td>
                  <a href={`/runs/${run.id}/download/${encodeURIComponent(a.name)}`}>{a.name}</a>
                </td>
                <td className="num">{(a.bytes / 1024).toFixed(1)} KB</td>
                <td
                  className="mono muted small"
                  title={t(lang, "fingerprintHint")}
                >
                  {a.bytes_sha256.slice(0, 16)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {diagnostics.length > 0 && (
        <p className="muted small">
          {/* B10: `run_metrics.json` is wall-clock, RSS and per-stage timings for
              deciding whether to port the compute engine. It is not a deliverable,
              and listing it beside the finance file invited somebody to open it
              looking for numbers. Still reachable — hidden, not removed. */}
          {t(lang, "diagnostics")}{" "}
          {diagnostics.map((a, i) => (
            <span key={a.name}>
              {i > 0 && ", "}
              <a href={`/runs/${run.id}/download/${encodeURIComponent(a.name)}`}>{a.name}</a>
            </span>
          ))}
          .
        </p>
      )}

      <h2>{t(lang, "exceptions")}</h2>
      <ExceptionQueue
        runId={run.id}
        sheets={sheets}
        lang={lang}
        canDecide={me.role !== "recon.viewer"}
        openOnly={open === "1"}
      />

      <h2>{t(lang, "runLog")}</h2>
      <RunLog runId={run.id} complete={!run.in_flight} />
    </>
  );
}

/**
 * Did this run have NOTHING to compare against, as opposed to gaps in a comparison
 * that partly happened? `_tie` emits exactly this sentence per store when no
 * reference matched it, so every line being of that shape means no figure reached
 * any store.
 */
function nothingWasSupplied(unverified: string[]): boolean {
  return unverified.every((line) => line.endsWith("no team reference found"));
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
